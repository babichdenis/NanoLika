"""Bound HTTP bodies before framework parsers allocate upload files or form fields."""

from __future__ import annotations

from tempfile import SpooledTemporaryFile

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyLimitMiddleware:
    """Validate a bounded, disk-spooled body before invoking the application.

    Counting a wrapped receive alone is insufficient: a framework may catch its
    exception and turn it into a generic multipart 400. Preflight also guarantees
    that no endpoint runs with a body whose declared length lied. Accepted bodies
    are replayed in small chunks; large uploads spill to disk instead of RAM.
    """

    def __init__(self, app: ASGIApp, max_bytes: int, overrides: dict[str, int] | None = None):
        self.app = app
        self.max_bytes = max(0, max_bytes)
        # A path may carry a larger body than the rest (a chat attachment, not a prompt).
        self.overrides = {path: max(0, value) for path, value in (overrides or {}).items()}

    def _limit(self, scope: Scope) -> int:
        path = str(scope.get("path") or "")
        for prefix, value in self.overrides.items():
            if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
                return value
        return self.max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        max_bytes = self._limit(scope)
        for name, value in scope.get("headers", []):
            if name.lower() != b"content-length":
                continue
            try:
                declared = int(value)
            except ValueError:
                continue
            if declared > max_bytes:
                await self.reject(scope, receive, send, max_bytes)
                return

        with SpooledTemporaryFile(max_size=min(1024 * 1024, max(1, max_bytes))) as body:
            size = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > max_bytes:
                    await self.reject(scope, receive, send, max_bytes)
                    return
                body.write(chunk)
                if not message.get("more_body", False):
                    break
            body.seek(0)
            finished = False

            async def replay() -> Message:
                nonlocal finished
                if finished:
                    return await receive()
                chunk = body.read(64 * 1024)
                more = body.tell() < size
                finished = not more
                return {"type": "http.request", "body": chunk, "more_body": more}

            await self.app(scope, replay, send)

    async def reject(self, scope: Scope, receive: Receive, send: Send, limit: int = 0) -> None:
        scope.setdefault("state", {})["error_code"] = "too_large"
        response = JSONResponse(
            {
                "error": {
                    "code": "too_large",
                    "message": f"This relay accepts request bodies up to {limit or self.max_bytes} bytes",
                    "max_bytes": limit or self.max_bytes,
                }
            },
            status_code=413,
        )
        await response(scope, receive, send)
