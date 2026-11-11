"""Admission evidence and structural validation for supplier routes.

A passing smoke suite is evidence for named capabilities, not a universal quality score.
Certificates apply to one supplier/model pair and expire. No customer prompts are stored.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

SUITE_VERSION = 1
STREAM_PREFETCH_SECONDS = 30
STREAM_COMPLETION_SECONDS = 180


def usable_message(message: Any) -> bool:
    if not isinstance(message, dict):
        return False
    text = message.get("content")
    if isinstance(text, str) and text.strip():
        return True
    if isinstance(text, list) and any(isinstance(p, dict) and str(p.get("text") or "").strip() for p in text):
        return True
    calls = message.get("tool_calls")
    if not calls and isinstance(message.get("function_call"), dict):
        calls = [{"function": message["function_call"]}]
    if isinstance(calls, list):
        for call in calls:
            function = call.get("function") if isinstance(call, dict) else None
            if not isinstance(function, dict) or not function.get("name"):
                continue
            try:
                arguments = json.loads(function.get("arguments", ""))
            except (TypeError, ValueError):
                continue
            if isinstance(arguments, dict):
                return True
    return False


def valid_chat_response(data: Any) -> bool:
    if not isinstance(data, dict) or data.get("error"):
        return False
    choices = data.get("choices")
    return isinstance(choices, list) and any(isinstance(c, dict) and usable_message(c.get("message")) for c in choices)


class QualityEvidence:
    def __init__(self, path: str = "") -> None:
        self.path = Path(path) if path else None
        self.rows: dict[str, dict] = {}
        self._stamp: tuple[int, int] | None = None
        self.error = ""
        self.checked_at = 0.0

    def load(self) -> None:
        if self.path is None:
            return
        try:
            st = self.path.stat()
            stamp = (st.st_mtime_ns, st.st_size)
            if stamp == self._stamp:
                return
            raw = json.loads(self.path.read_text())
            if raw.get("suite_version") != SUITE_VERSION or not isinstance(raw.get("routes"), dict):
                raise ValueError("unsupported quality evidence")
            rows = raw["routes"]
            for value in rows.values():
                if not isinstance(value, dict):
                    raise ValueError("invalid quality row")
            self.rows, self._stamp, self.error = rows, stamp, ""
            self.checked_at = float(raw.get("checked_at", 0))
            if not math.isfinite(self.checked_at):
                raise ValueError("invalid evidence timestamp")
        except (OSError, ValueError, TypeError, AttributeError):
            # An unreadable or removed certificate must not leave a stale approval alive.
            self.rows, self._stamp, self.error = {}, None, "quality evidence unavailable"
            self.checked_at = 0.0

    def check(self, pair: str, capabilities: set[str], required: bool) -> tuple[bool, str, str]:
        self.load()
        row = self.rows.get(pair)
        if row is None:
            return (not required, "нет действующей проверки качества" if required else "", "unverified")
        try:
            until = float(row.get("expires_at", 0))
        except (ValueError, TypeError):
            until = 0
        if not math.isfinite(until) or until <= time.time():
            return (not required, "проверка качества устарела" if required else "", "expired")
        checks = row.get("checks")
        if not isinstance(checks, dict):
            return False, "повреждены результаты проверки качества", "failed"
        # A known failed capability is excluded even in staged rollout mode.
        if any(checks.get(c) is False for c in capabilities):
            return False, "не пройдена проверка качества для задачи", "failed"
        passed = all(checks.get(c) is True for c in capabilities)
        if required and not passed:
            return False, "не проверены нужные возможности модели", "unverified"
        return True, "", "passed" if passed else "unverified"


class StreamCheck:
    """Validate SSE completion and assembled tool arguments, without retaining prose."""

    def __init__(self) -> None:
        self.text = False
        self.finished = False
        self.calls: dict[tuple[int, int], dict[str, str]] = {}

    def feed(self, line: str) -> bool:
        import httpx

        if not line.startswith("data:"):
            return False
        raw = line[5:].strip()
        if raw == "[DONE]":
            self.finished = True
            return False
        try:
            data = json.loads(raw)
        except ValueError:
            raise httpx.ReadError("Supplier sent invalid SSE JSON") from None
        if not isinstance(data, dict) or data.get("error"):
            raise httpx.ReadError("Supplier stream reported an error")
        progress = False
        choices = data.get("choices") or []
        if not isinstance(choices, list):
            raise httpx.ReadError("Supplier choices must be a list")
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            if choice.get("finish_reason"):
                self.finished = True
            delta = choice.get("delta") or choice.get("message") or {}
            if not isinstance(delta, dict):
                continue
            if isinstance(delta.get("content"), str) and delta["content"].strip():
                self.text = True
                progress = True
            if delta.get("reasoning_content"):
                progress = True
            calls = delta.get("tool_calls") or []
            if not calls and isinstance(delta.get("function_call"), dict):
                calls = [{"index": 0, "function": delta["function_call"]}]
            if not isinstance(calls, list):
                raise httpx.ReadError("Supplier tool calls must be a list")
            for call in calls:
                if not isinstance(call, dict):
                    continue
                key = (choice.get("index", 0), call.get("index", 0))
                if not all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 128 for v in key):
                    raise httpx.ReadError("Invalid supplier tool index")
                function = call.get("function") or {}
                if not isinstance(function, dict):
                    continue
                saved = self.calls.setdefault(key, {"name": "", "arguments": ""})
                for field in ("name", "arguments"):
                    if isinstance(function.get(field), str):
                        saved[field] += function[field]
                        progress = True
                if len(saved["arguments"]) > 1_000_000:
                    raise httpx.ReadError("Supplier tool arguments exceed validation limit")
        return progress

    def valid(self) -> bool:
        tools = [{"function": f} for f in self.calls.values()]
        return self.finished and (self.text or usable_message({"tool_calls": tools}))


async def validated_stream(response, complete, failure):
    """Fail over before visible output; count success only after a valid completed stream."""
    import asyncio

    import httpx

    deadline = time.monotonic() + STREAM_COMPLETION_SECONDS
    lines = response.aiter_lines()
    monitor = StreamCheck()
    pending: list[bytes] = []
    total = 0
    try:
        async with asyncio.timeout(STREAM_PREFETCH_SECONDS):
            async for line in lines:
                block = (line + "\n").encode()
                total += len(block)
                if total > 1_000_000:
                    raise httpx.ReadError("Supplier prelude exceeds validation limit")
                pending.append(block)
                if monitor.feed(line):
                    break
            else:
                raise httpx.ReadError("Supplier stream had no usable output")
    except TimeoutError:
        raise httpx.ReadTimeout("Supplier stream had no usable output before deadline") from None

    class CheckedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            try:
                for block in pending:
                    yield block
                while True:
                    try:
                        async with asyncio.timeout(max(0, deadline - time.monotonic())):
                            line = await anext(lines)
                    except StopAsyncIteration:
                        break
                    except TimeoutError:
                        raise httpx.ReadTimeout("Supplier stream exceeded completion deadline") from None
                    monitor.feed(line)
                    yield (line + "\n").encode()
                if not monitor.valid():
                    raise httpx.ReadError("Supplier stream ended without a complete usable answer")
                complete()
            except httpx.HTTPError:
                failure()
                raise
            finally:
                await response.aclose()

        async def aclose(self):
            await response.aclose()

    # aiter_lines already decompressed the supplier bytes.
    headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "content-encoding"}}
    return httpx.Response(response.status_code, headers=headers, stream=CheckedStream(), request=response.request)
