"""The web client on the relay: `/api/*` goes to the account's runtime.

The web app (`/web/`) is a shell: it calls `/api/threads`, `/api/events` and the rest on its
own origin. On a self-hosted runtime that origin *is* the runtime; on the relay it is not,
so the page fails with a network error the moment it says anything (2026-11-05, the PWA on
an iPhone: "Load failed").

This module makes the relay a door to the runtime a person already owns: the browser keeps
its own session cookie, the runtime token never reaches the page, and a session can only
reach the account it belongs to.

First slice: the runtime on the relay (`nanolika-bot:8799`) is the account's runtime; the
same process also serves Telegram and MAX, so all doors share one memory and one balance.
Per-account workers (a pool, a scheduler) are the next step, not this one.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from typing import Any

import httpx
import logging
import websockets
from fastapi import Request, WebSocket
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import JSONResponse, Response

from .browser_auth import SESSION_COOKIE
from .browser_session_store import browser_store

log = logging.getLogger("nanomuse_cloud.web_runtime")

#: where the runtime of the account lives; the bot container is on the same docker network
RUNTIME_BASE = os.environ.get("WEB_RUNTIME_BASE", "http://nanolika-bot:8799").rstrip("/")
RUNTIME_WS = os.environ.get("WEB_RUNTIME_WS", RUNTIME_BASE.replace("http", "ws", 1) + "/ws").strip()
#: the runtime's own access token; the relay holds it, the browser never sees it
RUNTIME_TOKEN = os.environ.get("WEB_RUNTIME_TOKEN", "").strip()
#: accounts the first slice serves (comma-separated ids); empty means "nobody yet" — fail closed
OWNERS = tuple(x.strip() for x in os.environ.get("WEB_RUNTIME_ACCOUNTS", "").split(",") if x.strip())

#: headers a browser may not pass through: the runtime decides its own framing and encoding
DROP_REQUEST = {
    "host",
    "content-length",
    "connection",
    "keep-alive",
    "authorization",
    "cookie",
    "accept-encoding",
}
DROP_RESPONSE = {"content-length", "content-encoding", "transfer-encoding", "connection"}


def _runtime_headers(request: Request) -> dict[str, str]:
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in DROP_REQUEST
    }
    headers["Authorization"] = f"Bearer {RUNTIME_TOKEN}"
    return headers


async def _caller_for(cloud: Any, request: Request) -> Any:
    """The account behind the browser session, or None."""
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token or not RUNTIME_TOKEN:
        return None
    # The browser session cookie is an opaque session id, not an API key: the console
    # resolves it through the session store (2026-11-05 — `authenticate` alone found no
    # account, so `/web/` fell back to the lite chat).
    try:
        store = browser_store(cloud)
        resolved = await asyncio.to_thread(store.caller, token)
        if resolved is None:
            # a session issued before the store was introduced lives in the legacy table
            resolved = await asyncio.to_thread(store.legacy_caller, token)
        if resolved is not None:
            return resolved
        log.info("web ui: session %s… resolved to nobody", token[:6])
    except Exception as exc:  # noqa: BLE001 — fall through to the key path
        log.info("web ui: session lookup failed: %s", type(exc).__name__)
    try:
        return await asyncio.to_thread(cloud.authenticate, token)
    except Exception:  # noqa: BLE001 — an unknown or revoked session is simply not in
        return None



class WebUiProxy(BaseHTTPMiddleware):
    """Serve the runtime's own web client at `/web/` for a signed-in account.

    The relay's `/web/` is a static lite chat; the full client (memory, tools, files) is
    the runtime's page. The static mount matches first, so this runs as middleware — the
    same door, one layer earlier. An account that is not allowed, or a runtime that is
    down, falls through to the static page instead of an error.
    """

    def __init__(self, app: Any, cloud: Any) -> None:  # noqa: ANN401
        super().__init__(app)
        self.cloud = cloud

    async def dispatch(self, request: Request, call_next: Any) -> Any:  # noqa: ANN401
        path = request.url.path
        if not (path == "/web" or path.startswith("/web/")):
            return await call_next(request)
        token = request.cookies.get(SESSION_COOKIE, "")
        if not token or not RUNTIME_TOKEN:
            return await call_next(request)
        caller = await _caller_for(self.cloud, request)
        if caller is None:
            return await call_next(request)
        log.info("web ui: account=%s allowed=%s", caller.account_id, caller.account_id in OWNERS)
        if caller.account_id not in OWNERS:
            return await call_next(request)
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in DROP_REQUEST
        }
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
                answer = await client.request(
                    request.method,
                    RUNTIME_BASE + path,
                    params=request.query_params,
                    headers=headers,
                    content=await request.body(),
                )
        except httpx.HTTPError:
            return await call_next(request)
        if answer.status_code >= 400:
            return await call_next(request)
        return Response(
            content=answer.content,
            status_code=answer.status_code,
            headers={
                key: value
                for key, value in answer.headers.items()
                if key.lower() not in DROP_RESPONSE
            },
            media_type=answer.headers.get("content-type"),
        )


def install_web_runtime(app: Any, cloud: Any, settings: Any) -> None:
    """Mount `/api/*` and `/ws` as the runtime door, and `/web/` as its page."""

    # /web/ stays the static page for now: the working pattern is the Telegram mini app
    # (telegram.html), which signs in with initData and keeps an API key. Copy that next.

    @app.websocket("/ws")
    async def web_runtime_ws(websocket: WebSocket) -> None:
        """The chat's events and streaming: the same door, for WebSocket.

        The browser authenticates with its own session cookie on the handshake; the frame
        it sends first (its idea of a token) is consumed here and never forwarded — the
        relay authenticates upstream itself, so the runtime token stays on the server."""
        caller = await _caller_for(cloud, websocket)
        if caller is None or caller.account_id not in OWNERS:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        try:
            await asyncio.wait_for(websocket.receive_text(), timeout=15)
        except Exception:  # noqa: BLE001 — no auth frame, no session
            await websocket.close(code=4401)
            return
        try:
            upstream = await websockets.connect(RUNTIME_WS, max_size=None, open_timeout=10)
        except Exception:  # noqa: BLE001 — the runtime is not there right now
            await websocket.close(code=4502)
            return
        await upstream.send(json.dumps({"kind": "auth", "token": RUNTIME_TOKEN}))

        async def to_upstream() -> None:
            while True:
                await upstream.send(await websocket.receive_text())

        async def to_client() -> None:
            async for message in upstream:
                await websocket.send_text(
                    message if isinstance(message, str) else message.decode("utf-8", "replace")
                )

        tasks = [asyncio.create_task(to_upstream()), asyncio.create_task(to_client())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            with contextlib.suppress(Exception):
                await upstream.close()
            with contextlib.suppress(Exception):
                await websocket.close()

    @app.api_route(
        "/api/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    async def web_runtime(path: str, request: Request) -> Response:
        caller = await _caller_for(cloud, request)
        if caller is None:
            return JSONResponse({"detail": "sign in to NanoLika"}, status_code=401)
        if caller.account_id not in OWNERS:
            return JSONResponse({"detail": "this account has no runtime here yet"}, status_code=403)
        body = await request.body()
        url = f"{RUNTIME_BASE}/api/{path}"
        try:
            async with httpx.AsyncClient(timeout=300.0, follow_redirects=False) as client:
                answer = await client.request(
                    request.method,
                    url,
                    params=request.query_params,
                    headers=_runtime_headers(request),
                    content=body,
                )
        except httpx.HTTPError as exc:
            return JSONResponse(
                {"detail": f"the runtime is not reachable: {type(exc).__name__}"},
                status_code=502,
            )
        headers = {
            key: value
            for key, value in answer.headers.items()
            if key.lower() not in DROP_RESPONSE
        }
        return Response(
            content=answer.content,
            status_code=answer.status_code,
            headers=headers,
            media_type=answer.headers.get("content-type"),
        )


__all__ = ["install_web_runtime", "RUNTIME_BASE", "OWNERS"]
