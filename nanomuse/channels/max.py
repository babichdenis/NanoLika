"""MAX Bot API over a verified HTTPS webhook, using the normal channel pairing gate."""

from __future__ import annotations

import asyncio
import hmac
import json
import re
import ssl
from collections import deque
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, Request

from nanomuse.channels.approval_choices import choices
from nanomuse.channels.base import Channel, ChannelBus, Field, InboundMessage, json_object
from nanomuse.channels.link_code import ChatLink, answer_code, as_code
from nanomuse.logger import logger

API = "https://platform-api2.max.ru"
MAX_BODY_BYTES = 256 * 1024


class MaxError(RuntimeError):
    pass


class MaxChannel(Channel):
    name = "max"
    label = "MAX"
    sdk_module = ""
    extra = ""
    supports_edit = True
    console_url = "https://business.max.ru/"
    fields = (
        Field("bot_token", "Bot token", "secret", required=True),
        Field("webhook_url", "HTTPS webhook URL", required=True),
        Field("webhook_secret", "Webhook secret", "secret", required=True),
        Field("ca_file", "Additional CA certificate file"),
    )

    def __init__(self, settings: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(settings)
        self._transport = transport
        self._http: httpx.AsyncClient | None = None
        self._seen: set[str] = set()
        self._order: deque[str] = deque()
        self._streams: dict[str, str] = {}
        self._typing: dict[str, asyncio.Task[None]] = {}
        self._approval_cards: dict[tuple[str, str], tuple[str, str, list]] = {}
        # the relay's linking calls (link_code.py), made on the first code a chat sends
        self._chat_link: ChatLink | None = None
        self._link_tasks: set[asyncio.Task[Any]] = set()

    def _make_http(self) -> httpx.AsyncClient:
        ctx = ssl.create_default_context()
        ca = str(
            self.settings.get("ca_file")
            or Path(__file__).with_name("certificates") / "russian-root.pem"
        ).strip()
        if ca:
            ctx.load_verify_locations(ca)
        return httpx.AsyncClient(
            base_url=API,
            headers={"Authorization": str(self.settings.get("bot_token") or "")},
            verify=ctx,
            timeout=20,
            transport=self._transport,
            follow_redirects=False,
        )

    async def _call(self, method: str, path: str, **kwargs: Any) -> dict:
        if self._http is None:
            raise MaxError("MAX is not connected")
        try:
            r = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError:
            raise MaxError("MAX connection failed") from None
        data = json_object(r)
        if r.status_code >= 400 or data.get("success") is False:
            # Never include headers, arbitrary provider bodies, tokens or user content.
            raise MaxError(f"MAX request failed (HTTP {r.status_code})")
        return data

    async def verify(self) -> str:
        own = self._http is None
        if own:
            self._http = self._make_http()
        try:
            me = await self._call("GET", "/me")
            if not me.get("is_bot"):
                raise MaxError("MAX credentials do not identify a bot")
            return str(me.get("username") or "MAX bot")
        finally:
            if own and self._http is not None:
                await self._http.aclose()
                self._http = None

    async def start(self, bus: ChannelBus) -> None:
        url = str(self.settings.get("webhook_url") or "")
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise MaxError("MAX requires a public HTTPS webhook URL without credentials or query")
        secret = str(self.settings.get("webhook_secret") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", secret):
            raise MaxError("MAX webhook secret must contain 32 to 256 URL-safe characters")
        await super().start(bus)
        self._http = self._make_http()
        try:
            name = await self.verify()
            subscriptions = await self._call("GET", "/subscriptions")
            if any(s.get("url") != url for s in subscriptions.get("subscriptions", [])):
                raise MaxError("MAX has another webhook; review it before replacing it")
            await self._call(
                "POST",
                "/subscriptions",
                json={
                    "url": url,
                    "secret": secret,
                    "update_types": ["message_created", "message_callback", "bot_started"],
                },
            )
            bus.set_status(self.name, "connected", name)
        except Exception:
            await self.stop()
            raise

    async def stop(self) -> None:
        tasks = list(self._typing.values())
        self._typing.clear()
        # a code still being handed to the relay would answer into a closed connection
        tasks += list(self._link_tasks)
        self._link_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        self._streams.clear()

    def _duplicate(self, key: str) -> bool:
        if key in self._seen:
            return True
        self._seen.add(key)
        self._order.append(key)
        while len(self._order) > 4096:
            self._seen.discard(self._order.popleft())
        return False

    def handle_update(self, data: dict) -> None:
        kind = data.get("update_type")
        if kind == "bot_started":
            user = data.get("user") or {}
            chat = str(data.get("chat_id") or "")
            sender = str(user.get("user_id") or "")
            key = f"start:{chat}:{sender}:{data.get('timestamp')}"
            if chat and sender and not self._duplicate(key):
                self.bus.inbound(
                    InboundMessage(channel=self.name, chat_id=chat, sender_id=sender, text="/start")
                )
            return
        message = data.get("message") or {}
        recipient = message.get("recipient") or {}
        # Private, explicitly paired chats only in this first integration.
        if recipient.get("chat_type") != "dialog":
            return
        chat = str(recipient.get("chat_id") or "")
        body = message.get("body") or {}
        if kind == "message_callback":
            callback = data.get("callback") or {}
            sender = str((callback.get("user") or {}).get("user_id") or "")
            try:
                value = json.loads(callback.get("payload") or "{}")
            except (ValueError, TypeError):
                return
            key = str(callback.get("callback_id") or "")
            if (
                chat
                and sender
                and key
                and isinstance(value, dict)
                and not self._duplicate("callback:" + key)
            ):
                if "menu" in value:
                    value["_menu_message_id"] = str(body.get("mid") or "")
                self.bus.action(self.name, chat, sender, value)
            return
        if kind != "message_created":
            return
        user = message.get("sender") or {}
        if user.get("is_bot"):
            return
        sender = str(user.get("user_id") or "")
        mid = str(body.get("mid") or "")
        text = str(body.get("text") or "").strip()
        try:
            from .touches import note as _note

            _note("max", sender, chat, text)
        except Exception:
            pass
        if chat and sender and mid and text and not self._duplicate("message:" + mid):
            inbound = InboundMessage(
                channel=self.name,
                chat_id=chat,
                sender_id=sender,
                sender_name=str(user.get("name") or ""),
                text=text,
                message_id=mid,
            )
            if self._link_first(inbound):
                return
            self.bus.inbound(inbound)

    def _link(self) -> ChatLink:
        """The relay's two linking calls, with the settings the sign-in door already uses
        (``cloud_url`` and ``cloud_bot_token``); made once, on the first code."""
        if self._chat_link is None:
            self._chat_link = ChatLink(self.name, self.settings, self._transport)
        return self._chat_link

    def _link_first(self, msg: InboundMessage) -> bool:
        """A chat with no account yet may send the six characters the app showed.

        True when the message was taken for that: the answer comes from the relay's call,
        which is not quick enough to hold the webhook, so `_answer_code` runs on its own
        and hands the message on when it turns out not to be a code after all."""
        if not as_code(msg.text) or not self._link().configured():
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no loop: the webhook path always has one, a test may not
            return False
        task = loop.create_task(self._answer_code(msg))
        self._link_tasks.add(task)
        task.add_done_callback(self._link_tasks.discard)
        return True

    async def _answer_code(self, msg: InboundMessage) -> None:
        bus = self.bus
        if bus is None:
            return
        try:
            answer = await answer_code(
                self._link(), msg.sender_id, msg.text, "", getattr(bus, "lang", "") or ""
            )
        except Exception as exc:  # noqa: BLE001 — a code must never break the chat
            logger.warning("max: link code failed: {}", type(exc).__name__)
            answer = ""
        if not answer:
            bus.inbound(msg)
            return
        with suppress(MaxError):
            await self.send(msg.chat_id, answer)

    async def typing(self, chat_id: str, on: bool) -> None:
        current = self._typing.get(chat_id)
        if on and current is not None and not current.done():
            return
        current = self._typing.pop(chat_id, None)
        if current is not None:
            current.cancel()
            await asyncio.gather(current, return_exceptions=True)
        if on:
            self._typing[chat_id] = asyncio.create_task(self._typing_loop(chat_id))

    async def _typing_loop(self, chat_id: str) -> None:
        # Activity expires on MAX; refresh only while the manager reports working.
        while True:
            with suppress(MaxError):
                await self._call("POST", f"/chats/{chat_id}/actions", json={"action": "typing_on"})
            await asyncio.sleep(4.5)

    async def send(self, chat_id: str, text: str, *, files: list[str] | None = None) -> str:
        if files:
            raise MaxError("MAX file delivery is not enabled")
        last = ""
        for start in range(0, len(text), 4000):
            result = await self._call(
                "POST",
                "/messages",
                params={"chat_id": chat_id},
                json={"text": text[start : start + 4000]},
            )
            last = str(((result.get("message") or {}).get("body") or {}).get("mid") or "")
        return last

    async def stream(self, chat_id: str, stream_id: str, text: str, *, done: bool) -> None:
        if not text.strip():
            return
        if len(text) > 4000:
            if done:
                if stream_id in self._streams:
                    await self._call(
                        "PUT",
                        "/messages",
                        params={"message_id": self._streams.pop(stream_id)},
                        json={"text": text[:4000]},
                    )
                    await self.send(chat_id, text[4000:])
                else:
                    await self.send(chat_id, text)
            return
        mid = self._streams.get(stream_id)
        if mid:
            await self._call("PUT", "/messages", params={"message_id": mid}, json={"text": text})
        else:
            mid = await self.send(chat_id, text)
            if mid:
                self._streams[stream_id] = mid
        if done:
            self._streams.pop(stream_id, None)

    async def ask(self, chat_id: str, approval_id: str, text: str, lang: str = "") -> None:
        await self.ask_scoped(chat_id, approval_id, text, lang, ["once"])

    async def ask_scoped(
        self, chat_id: str, approval_id: str, text: str, lang: str, scopes: list[str]
    ) -> None:
        rows = [
            [
                {
                    "type": "callback",
                    "text": label,
                    "payload": json.dumps(
                        {
                            "approval": approval_id,
                            "decision": "deny" if code == "n" else "allow",
                            "scope": scope,
                        }
                    ),
                }
            ]
            for code, label, scope in choices(scopes, lang)
        ]
        rows = [rows[0] + rows[-1]] + rows[1:-1]
        rows.append(
            [
                {
                    "type": "callback",
                    "text": {"ru": "Подробности", "zh": "详情"}.get(lang, "Details"),
                    "payload": json.dumps({"approval": approval_id, "decision": "details"}),
                }
            ]
        )
        result = await self._call(
            "POST",
            "/messages",
            params={"chat_id": chat_id},
            json={
                "text": text[:4000],
                "attachments": [{"type": "inline_keyboard", "payload": {"buttons": rows}}],
            },
        )

        self._approval_cards[(chat_id, approval_id)] = (
            str(((result.get("message") or {}).get("body") or {}).get("mid") or ""),
            text,
            rows,
        )
        while len(self._approval_cards) > 256:
            self._approval_cards.pop(next(iter(self._approval_cards)))

    async def expand_approval(
        self, chat_id: str, approval_id: str, details: str, lang: str, scopes: list[str]
    ) -> None:
        card = self._approval_cards.get((chat_id, approval_id))
        if card is None or not card[0]:
            # A recoverable fallback still keeps decision buttons below the details.
            await self.ask_scoped(chat_id, approval_id, details, lang, scopes)
            return
        message_id, original, rows = card
        prompt = {
            "ru": "Решение ещё не принято. Выберите действие ниже.",
            "zh": "尚未授权，请在下方选择。",
        }.get(lang, "No decision yet. Choose an action below.")
        body = original[:1100] + "\n\n" + details[:2600] + "\n\n" + prompt
        decision_rows = rows[:-1]
        await self._call(
            "PUT",
            "/messages",
            params={"message_id": message_id},
            json={
                "text": body,
                "attachments": [{"type": "inline_keyboard", "payload": {"buttons": decision_rows}}],
            },
        )


async def receive_webhook(channel: MaxChannel | None, request: Request) -> dict:
    if channel is None or channel._http is None:
        raise HTTPException(503, "MAX is not configured")
    expected = str(channel.settings.get("webhook_secret") or "")
    supplied = request.headers.get("X-Max-Bot-Api-Secret", "")
    if not expected or not hmac.compare_digest(expected.encode(), supplied.encode()):
        raise HTTPException(403, "Webhook authentication failed")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_BODY_BYTES:
            raise HTTPException(413, "Webhook too large")
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError
        # Invalid nested shapes must never reach the model or its tools.
        if data.get("update_type") == "message_callback":
            callback = data.get("callback") or {}
            key = str(callback.get("callback_id") or "")
            if key:
                with suppress(Exception):
                    await channel._call("POST", "/answers", params={"callback_id": key}, json={})
        channel.handle_update(data)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(400, "Malformed MAX update") from None
    return {"ok": True}
