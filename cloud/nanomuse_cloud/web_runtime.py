"""The web client on the relay: `/api/*` and `/ws` go to the account's own runtime.

The browser keeps its own session cookie. The relay proves the session, asks the worker
for that account's ``MuseService`` and keeps the runtime token and the account's Cloud key
on the server. One worker process holds many accounts; the account is chosen here, from
the verified session, never from the request body.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx
import websockets
from fastapi import Request, WebSocket
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from .browser_auth import SESSION_COOKIE
from .browser_session_store import browser_store

log = logging.getLogger("nanomuse_cloud.web_runtime")

#: the account worker (MuseService per account inside one process), on the docker network
RUNTIME_BASE = os.environ.get("WEB_RUNTIME_BASE", "http://cloud-worker:8789").rstrip("/")
RUNTIME_WS = os.environ.get("WEB_RUNTIME_WS", RUNTIME_BASE.replace("http", "ws", 1) + "/ws").strip()
#: the shared secret of that worker (`NANOMUSE_WORKER_SECRET` on its side); empty = loopback trust
WORKER_SECRET = os.environ.get("WEB_RUNTIME_SECRET", "").strip()
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
    "x-worker-secret",
}
DROP_RESPONSE = {"content-length", "content-encoding", "transfer-encoding", "connection"}

#: short-lived Cloud keys handed to the worker: one per account, reused for hours
_KEYS: dict[str, tuple[str, float]] = {}
_KEY_TTL_S = 6 * 3600


def _worker_auth() -> dict[str, str]:
    """The worker's own header, when the deployment shares a secret with it."""
    return {"X-Worker-Secret": WORKER_SECRET} if WORKER_SECRET else {}


#: The keys handed to the worker, kept on disk: a restart must not mint a new one while the
#: worker still holds the old, or the agent answers "this key is no longer valid".
_KEYS_PATH = os.environ.get("CLOUD_RUNTIME_KEYS", "/srv/nanomuse-cloud/data/runtime-keys.json")


def _load_keys() -> dict[str, tuple[str, float]]:
    try:
        raw = json.loads(Path(_KEYS_PATH).read_text())
    except (OSError, ValueError):
        return {}
    out: dict[str, tuple[str, float]] = {}
    for key, value in raw.items():
        if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
            out[str(key)] = (value[0], float(value[1]))
    return out


def _save_keys(keys: dict[str, tuple[str, float]]) -> None:
    try:
        Path(_KEYS_PATH).parent.mkdir(parents=True, exist_ok=True)
        Path(_KEYS_PATH).write_text(json.dumps({k: [v[0], v[1]] for k, v in keys.items()}))
    except OSError:
        log.warning("runtime keys could not be saved")


def _account_key(cloud: Any, account_id: str) -> str:
    now = time.time()
    if not _KEYS:
        _KEYS.update(_load_keys())
    held = _KEYS.get(account_id)
    if held is not None and held[1] > now + 60:
        return held[0]
    key, _caller = cloud._issue_key(account_id, "cloud worker", "runtime", expires_at=int(now) + _KEY_TTL_S)
    _KEYS[account_id] = (key, now + _KEY_TTL_S)
    _save_keys(_KEYS)
    return key


async def _caller_for(cloud: Any, request: Request | WebSocket) -> Any:
    """The account behind the request: an account key, or the browser session cookie.

    The apps send the key they already hold (`Authorization: Bearer`); the site and the
    mini-apps carry a session cookie instead. Either proves the same account, so a client
    that signed in with a key does not have to sign in again just to reach the agent.
    """
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            return await asyncio.to_thread(cloud.authenticate, auth[7:].strip())
        except Exception:  # noqa: BLE001 — an unknown or revoked key is simply not in
            return None
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
    headers = {key: value for key, value in request.headers.items() if key.lower() not in DROP_REQUEST}
    return headers


class RuntimePage(BaseHTTPMiddleware):
    """Serve the runtime's own page and its assets at `/web/` for a served account.

    The lite chat stays the page for everybody else (and for the Telegram and MAX entry
    pages); a pilot account gets the full client, which talks to `/api` and `/ws` on this
    same origin. A failure falls through to the static page instead of an error.
    """

    PREFIXES = ("/assets/", "/manifest.webmanifest", "/icon.svg", "/icon-192.png", "/icon-512.png")

    def __init__(self, app: Any, cloud: Any) -> None:  # noqa: ANN401
        super().__init__(app)
        self.cloud = cloud

    async def dispatch(self, request: Request, call_next: Any) -> Any:  # noqa: ANN401
        path = request.url.path
        if path in ("/web2", "/web2/"):
            # The full client cannot sign in through `/api` (that door needs a caller):
            # send the person to the site's own sign-in and come back with a session.
            if await _caller_for(self.cloud, request) is None:
                from urllib.parse import quote

                from fastapi.responses import RedirectResponse

                target = quote("https://cloud.nanolika.ru/web2/", safe="")
                return RedirectResponse(f"https://nanolika.ru/app/?signin=1&next={target}", status_code=302)
            return await call_next(request)
        page = path in ("/web", "/web/")
        if not page and not any(path.startswith(prefix) for prefix in self.PREFIXES):
            return await call_next(request)
        caller = await _caller_for(self.cloud, request)

        if caller is None or caller.account_id not in OWNERS:
            return await call_next(request)
        key = await asyncio.to_thread(_account_key, self.cloud, caller.account_id)
        headers = _runtime_headers(caller, request)
        headers.pop("accept-encoding", None)
        headers["X-Account"] = caller.account_id
        headers["X-Cloud-Key"] = key
        headers.update(_worker_auth())
        target = "/" if page else path
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
                answer = await client.get(f"{RUNTIME_BASE}{target}", headers=headers)
        except httpx.HTTPError:
            return await call_next(request)
        if answer.status_code >= 400:
            return await call_next(request)
        return Response(
            content=answer.content,
            status_code=answer.status_code,
            headers={key: value for key, value in answer.headers.items() if key.lower() not in DROP_RESPONSE},
            media_type=answer.headers.get("content-type"),
        )


def install_web_runtime(app: Any, cloud: Any, settings: Any) -> None:
    """Mount `/api/*` and `/ws` as the door to the account's own runtime."""

    app.add_middleware(RuntimePage, cloud=cloud)

    @app.websocket("/ws")
    async def web_runtime_ws(websocket: WebSocket) -> None:
        """The chat's events: a key in the first frame, or the session cookie."""
        # A cookie or an Authorization header proves the account before the handshake;
        # only when neither is there does the socket wait for the key in its first frame.
        caller = await _caller_for(cloud, websocket)
        await websocket.accept()
        if caller is None:
            first = ""
            try:
                first = await asyncio.wait_for(websocket.receive_text(), timeout=2.0)
            except Exception:  # noqa: BLE001 — no frame at all is the normal cookie case
                first = ""
            if first:
                try:
                    frame = json.loads(first)
                except ValueError:
                    frame = None
                if isinstance(frame, dict) and frame.get("kind") == "auth" and isinstance(frame.get("token"), str):
                    try:
                        caller = await asyncio.to_thread(cloud.authenticate, frame["token"])
                    except Exception:  # noqa: BLE001
                        caller = None
        if caller is None or caller.account_id not in OWNERS:
            await websocket.close(code=4401)
            return
        try:
            key = await asyncio.to_thread(_account_key, cloud, caller.account_id)
            upstream = await websockets.connect(
                RUNTIME_WS,
                max_size=None,
                open_timeout=10,
                additional_headers={"X-Account": caller.account_id, "X-Cloud-Key": key, **_worker_auth()},
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
                await websocket.send_text(message if isinstance(message, str) else message.decode("utf-8", "replace"))

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

    @app.delete("/api/cloud/channels/{channel}")
    async def web_runtime_unlink(channel: str, request: Request) -> Response:
        """Let a messenger go: the account's Telegram or MAX link is cut at the relay.

        The runtime proxies `/api/*` to the account's worker, which has no such call — the
        links live here — so this one route is answered by the relay itself.
        """
        caller = await _caller_for(cloud, request)
        if caller is None:
            return JSONResponse({"detail": "sign in to NanoLika"}, status_code=401)
        if channel not in ("telegram", "max"):
            return JSONResponse({"detail": "unknown messenger"}, status_code=404)
        try:
            answer = await asyncio.to_thread(cloud.unlink_channel, caller, channel)
        except Exception as exc:  # noqa: BLE001 — the refusal carries the reason
            detail = getattr(exc, "message", None) or str(exc) or "could not unlink"
            return JSONResponse({"detail": detail}, status_code=400)
        return JSONResponse(answer)

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
        headers.update(_worker_auth())
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
            headers={key: value for key, value in answer.headers.items() if key.lower() not in DROP_RESPONSE},
            media_type=answer.headers.get("content-type"),
        )


__all__ = ["install_web_runtime", "RUNTIME_BASE", "OWNERS"]
