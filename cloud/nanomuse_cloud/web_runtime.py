"""The web client on the relay: `/api/*` and `/ws` go to the account's own runtime.

The browser keeps its own session cookie. The relay proves the session, asks the worker
for that account's ``MuseService`` and keeps the runtime token and the account's Cloud key
on the server. One worker process holds many accounts; the account is chosen here, from
the verified session, never from the request body.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from typing import Any

import httpx
import websockets
from fastapi import Request, WebSocket
from fastapi.responses import JSONResponse, Response

from .browser_auth import SESSION_COOKIE
from .browser_session_store import browser_store

log = logging.getLogger("nanomuse_cloud.web_runtime")

#: the account worker (MuseService per account inside one process), on the docker network
RUNTIME_BASE = os.environ.get("WEB_RUNTIME_BASE", "http://cloud-worker:8789").rstrip("/")
RUNTIME_WS = os.environ.get("WEB_RUNTIME_WS", RUNTIME_BASE.replace("http", "ws", 1) + "/ws").strip()
#: accounts the pilot serves (comma-separated ids); empty means "nobody yet" — fail closed
OWNERS = tuple(x.strip() for x in os.environ.get("WEB_RUNTIME_ACCOUNTS", "").split(",") if x.strip())

DROP_REQUEST = {
    "host",
    "content-length",
    "connection",
    "keep-alive",
    "authorization",
    "cookie",
    "accept-encoding",
    "x-account",
    "x-cloud-key",
}
DROP_RESPONSE = {"content-length", "content-encoding", "transfer-encoding", "connection"}

#: short-lived Cloud keys handed to the worker: one per account, reused for hours
_KEYS: dict[str, tuple[str, float]] = {}
_KEY_TTL_S = 6 * 3600


def _account_key(cloud: Any, account_id: str) -> str:
    now = time.time()
    held = _KEYS.get(account_id)
    if held is not None and held[1] > now + 60:
        return held[0]
    key, _caller = cloud._issue_key(
        account_id, "cloud worker", "runtime", expires_at=int(now) + _KEY_TTL_S
    )
    _KEYS[account_id] = (key, now + _KEY_TTL_S)
    return key


async def _caller_for(cloud: Any, request: Request | WebSocket) -> Any:
    """The account behind the browser session, or None."""
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return None
    try:
        store = browser_store(cloud)
        resolved = await asyncio.to_thread(store.caller, token)
        if resolved is None:
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


def _runtime_headers(caller: Any, request: Request | WebSocket) -> dict[str, str]:
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in DROP_REQUEST
    }
    return headers


def install_web_runtime(app: Any, cloud: Any, settings: Any) -> None:
    """Mount `/api/*` and `/ws` as the door to the account's own runtime."""

    @app.websocket("/ws")
    async def web_runtime_ws(websocket: WebSocket) -> None:
        """The chat's events: the account is chosen by the session cookie on the handshake."""
        caller = await _caller_for(cloud, websocket)
        if caller is None or caller.account_id not in OWNERS:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        try:
            # the client sends its idea of a token first; the relay already proved the session
            await asyncio.wait_for(websocket.receive_text(), timeout=15)
        except Exception:  # noqa: BLE001 — no auth frame, no session
            await websocket.close(code=4401)
            return
        try:
            key = await asyncio.to_thread(_account_key, cloud, caller.account_id)
            upstream = await websockets.connect(
                RUNTIME_WS,
                max_size=None,
                open_timeout=10,
                additional_headers={"X-Account": caller.account_id, "X-Cloud-Key": key},
            )
        except Exception as exc:  # noqa: BLE001 — the worker is not there right now
            log.warning("web ws: worker unreachable: %s", type(exc).__name__)
            await websocket.close(code=4502)
            return

        async def to_worker() -> None:
            while True:
                await upstream.send(await websocket.receive_text())

        async def to_client() -> None:
            async for message in upstream:
                await websocket.send_text(
                    message if isinstance(message, str) else message.decode("utf-8", "replace")
                )

        tasks = [asyncio.create_task(to_worker()), asyncio.create_task(to_client())]
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
        key = await asyncio.to_thread(_account_key, cloud, caller.account_id)
        headers = _runtime_headers(caller, request)
        headers["X-Account"] = caller.account_id
        headers["X-Cloud-Key"] = key
        try:
            async with httpx.AsyncClient(timeout=300.0, follow_redirects=False) as client:
                answer = await client.request(
                    request.method,
                    f"{RUNTIME_BASE}/api/{path}",
                    params=request.query_params,
                    headers=headers,
                    content=await request.body(),
                )
        except httpx.HTTPError as exc:
            return JSONResponse(
                {"detail": f"the runtime is not reachable: {type(exc).__name__}"},
                status_code=502,
            )
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


__all__ = ["install_web_runtime", "RUNTIME_BASE", "OWNERS"]
