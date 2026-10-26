"""Verified website identity without exposing an API key to the marketing origin."""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

from fastapi import Depends, Request
from fastapi.responses import JSONResponse

from .browser_auth import SESSION_COOKIE, browser_marker
from .service import Caller, CloudError


def install(app, cloud, caller_dep) -> None:
    public = urlsplit(cloud.s.public_base)
    allowed = {"https://nanolika.ru", "https://www.nanolika.ru", "https://cloud.nanolika.ru", f"{public.scheme}://{public.netloc}"}

    def permitted(request: Request, *, required: bool = False) -> bool:
        origin = request.headers.get("origin", "")
        return origin in allowed if origin or required else True

    def response(request: Request, data: dict, status: int = 200):
        headers = {"Cache-Control": "private, no-store", "Vary": "Origin", "X-Content-Type-Options": "nosniff"}
        origin = request.headers.get("origin", "")
        if origin in allowed:
            headers.update({"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Credentials": "true"})
        return JSONResponse(data, status_code=status, headers=headers)

    @app.options("/v1/auth/site-session/logout", include_in_schema=False)
    @app.options("/v1/auth/browser-session", include_in_schema=False)
    async def preflight(request: Request):
        if not permitted(request, required=True):
            return response(request, {"error": {"code": "origin_denied"}}, 403)
        result = response(request, {})
        result.headers.update(
            {"Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type, Authorization"}
        )
        return result

    @app.get("/v1/auth/site-session", include_in_schema=False)
    async def status(request: Request):
        if not permitted(request):
            return response(request, {"error": {"code": "origin_denied"}}, 403)
        token = request.cookies.get(SESSION_COOKIE, "")
        if not token:
            return response(request, {"state": "signed-out"})
        try:
            caller = await asyncio.to_thread(cloud.authenticate, token)
        except CloudError as error:
            if error.status != 401:
                raise
            return browser_marker(response(request, {"state": "signed-out"}), cloud.s, signed_in=False)
        me = await asyncio.to_thread(cloud.me, caller)
        account = me.get("account", {})
        return response(request, {"state": "signed-in", "email": account.get("email") or account.get("hint") or ""})

    @app.post("/v1/auth/browser-session", include_in_schema=False)
    async def establish(request: Request, caller: Caller = Depends(caller_dep)):
        if not permitted(request):
            return response(request, {"error": {"code": "origin_denied"}}, 403)
        token = request.headers.get("authorization", "")[7:].strip()
        return browser_marker(response(request, {"ok": True}), cloud.s, api_key=token)

    @app.post("/v1/auth/site-session/logout", include_in_schema=False)
    async def logout(request: Request):
        if not permitted(request, required=True) or request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            return response(request, {"error": {"code": "origin_denied"}}, 403)
        token = request.cookies.get(SESSION_COOKIE, "")
        if token:
            try:
                caller = await asyncio.to_thread(cloud.authenticate, token)
            except CloudError as error:
                if error.status != 401:
                    raise
            else:
                await asyncio.to_thread(cloud.sign_out, caller)
                hub = getattr(app.state, "hub", None)
                if hub is not None:
                    await hub.drop_signed_out(caller.account_id)
        return browser_marker(response(request, {"state": "signed-out"}), cloud.s, signed_in=False)
