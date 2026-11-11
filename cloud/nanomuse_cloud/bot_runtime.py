"""Private messenger gateway to an account's server runtime.

Both the bot and the messenger identity are verified before the worker receives an
account header. Browser session or model-only API keys cannot use this endpoint.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
from typing import Any

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response

from .service import chat_identity, chat_surface

RUNTIME_BASE = os.environ.get("WEB_RUNTIME_BASE", "http://cloud-worker:8789").rstrip("/")
WORKER_SECRET = os.environ.get("WEB_RUNTIME_SECRET", "").strip()
MESSENGER_SECRET = os.environ.get("BOT_RUNTIME_SECRET", "").strip()
_OPERATION = re.compile(r"[A-Za-z0-9_-]{16,128}\Z")


def install_bot_runtime(app: Any, cloud: Any) -> None:
    async def route(request: Request, *, status: bool) -> Response:
        configured = str(cloud.s.bot_signin_token or "")
        if (
            not configured or not WORKER_SECRET or len(MESSENGER_SECRET) < 32
            or not hmac.compare_digest(request.headers.get("x-bot-token", ""), configured)
        ):
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        # Bound even chunked bodies before parsing; the relay is the public boundary.
        limit = (10 * 1024 * 1024 * 4 // 3) + 256 * 1024
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > limit:
                return JSONResponse({"detail": "request too large"}, status_code=413)
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return JSONResponse({"detail": "invalid request"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"detail": "invalid request"}, status_code=400)
        channel = str(body.get("channel") or "").strip().lower()
        sender = str(body.get("sender_id") or "")
        operation = str(body.get("operation_id") or "")
        auth = request.headers.get("authorization", "")
        if channel not in {"telegram", "max"} or not _OPERATION.fullmatch(operation) or not auth.lower().startswith("bearer "):
            return JSONResponse({"detail": "invalid request"}, status_code=400)
        key = auth[7:].strip()
        try:
            surface = chat_surface(channel)
            ident = chat_identity(surface, sender)
            ident = cloud._chat_key(surface, ident)
            caller, row = await asyncio.gather(
                asyncio.to_thread(cloud.authenticate, key),
                asyncio.to_thread(cloud._account_for, ident),
            )
            if row is None or str(row["id"]) != caller.account_id or caller.via != surface:
                return JSONResponse({"detail": "forbidden"}, status_code=403)
        except Exception:  # noqa: BLE001 - all invalid identities fail closed
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        headers = {
            "X-Worker-Secret": WORKER_SECRET, "X-Messenger-Secret": MESSENGER_SECRET,
            "X-Account": caller.account_id, "X-Cloud-Key": key,
        }
        if status:
            method, url, payload = "GET", f"{RUNTIME_BASE}/internal/messenger/messages/{operation}", None
        else:
            method, url = "POST", f"{RUNTIME_BASE}/internal/messenger/messages"
            payload = {k: body.get(k) for k in ("operation_id", "conversation_id", "text", "language", "files") if k in body}
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
                answer = await client.request(method, url, headers=headers, json=payload)
        except httpx.HTTPError:
            return JSONResponse({"detail": "runtime unavailable"}, status_code=503)
        return Response(content=answer.content, status_code=answer.status_code, media_type="application/json")

    @app.post("/v1/bot/runtime/message")
    async def bot_runtime_message(request: Request) -> Response:
        return await route(request, status=False)

    @app.post("/v1/bot/runtime/status")
    async def bot_runtime_status(request: Request) -> Response:
        return await route(request, status=True)
