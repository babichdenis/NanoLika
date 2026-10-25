"""Account-authenticated, single-use bot start links using the existing link-code store."""

from __future__ import annotations

import asyncio
import secrets

from fastapi import Depends, Request, Response

from .db import now
from .service import CHANNEL_CODE_TTL_S, Caller, Cloud, CloudError, _code_hash

BOT_URLS = {"telegram": "https://t.me/nanolika_bot", "max": "https://max.ru/id420517592685_bot"}
PREFIXES = {"telegram": "TG", "max": "MX"}


def create_start(cloud: Cloud, caller: Caller, channel: str) -> dict:
    if channel not in BOT_URLS:
        raise CloudError(400, "bad_channel", "Choose Telegram or MAX")
    if not cloud.s.link_code_ttl_s:
        raise CloudError(503, "link_off", "Channel connection is not available")
    account = cloud.db.account(caller.account_id)
    if account is None or account["disabled"]:
        raise CloudError(403, "account_disabled", "This account is unavailable")
    # 120 random bits, not an API key. The channel prefix is part of the stored hash.
    token = PREFIXES[channel] + secrets.token_hex(15).upper()
    cloud.db.add_link_code(_code_hash(cloud.s.hmac_key, token), caller.account_id, CHANNEL_CODE_TTL_S)
    cloud.db.add_event(caller.account_id, "link.start", channel)
    return {
        "channel": channel,
        "url": BOT_URLS[channel] + "?start=nl_" + token,
        "expires_at": now() + CHANNEL_CODE_TTL_S,
        "expires_in_s": CHANNEL_CODE_TTL_S,
    }


def install(app, cloud: Cloud, caller_dep) -> None:
    @app.post("/v1/link/start")
    async def start_link(request: Request, response: Response, caller: Caller = Depends(caller_dep)) -> dict:
        if len(await request.body()) > 1024:
            raise CloudError(413, "too_large", "Request is too large")
        try:
            body = await request.json()
        except ValueError as exc:
            raise CloudError(400, "bad_request", "Expected JSON") from exc
        if not isinstance(body, dict):
            raise CloudError(400, "bad_request", "Expected an object")
        response.headers["Cache-Control"] = "no-store"
        return await asyncio.to_thread(create_start, cloud, caller, str(body.get("channel") or ""))
