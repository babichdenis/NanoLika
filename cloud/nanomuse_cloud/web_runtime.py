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
import os
from typing import Any

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response

from .browser_auth import SESSION_COOKIE

#: where the runtime of the account lives; the bot container is on the same docker network
RUNTIME_BASE = os.environ.get("WEB_RUNTIME_BASE", "http://nanolika-bot:8799").rstrip("/")
#: the runtime's own access token; the relay holds it, the browser never sees it
RUNTIME_TOKEN = os.environ.get("WEB_RUNTIME_TOKEN", "").strip()
#: accounts the first slice serves (comma-separated ids); empty means "nobody yet"
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
    try:
        return await asyncio.to_thread(cloud.authenticate, token)
    except Exception:  # noqa: BLE001 — an unknown or revoked session is simply not in
        return None


def install_web_runtime(app: Any, cloud: Any, settings: Any) -> None:
    """Mount `/api/*` as the runtime door. Called from `create_app`."""

    @app.api_route(
        "/api/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    async def web_runtime(path: str, request: Request) -> Response:
        caller = await _caller_for(cloud, request)
        if caller is None:
            return JSONResponse({"detail": "sign in to NanoLika"}, status_code=401)
        if OWNERS and caller.account_id not in OWNERS:
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
