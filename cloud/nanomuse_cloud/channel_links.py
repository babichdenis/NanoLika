"""A chat tied to the account its person already has, by six characters instead of a word.

The app shows the code (`POST /v1/link/code`, asked for with the account's own key) and the
person sends it to the bot, which types it back into the relay (`POST /v1/bot/link`, proved
by the bot's shared secret). From then on the chat is one of that account's channels: the
bot's sign-in door finds the account for the chat, so what is said there is charged to its
allowance and the app and the chat are one person, one pool, one set of devices.

The other direction is older: a chat that came first makes its own account and hands the
app a code (`/v1/bot/link-code`, spent by `verify_code`'s `link`, `link_into` in
service.py). Both write the same row, one account's alias, so there is one answer to "who
is this chat" however the two were joined.

    POST   /v1/link/code        → {code, expires_at, expires_in_s}        the app asks
    GET    /v1/link/channels    → [{channel, identifier, label, linked_at}]
    DELETE /v1/link/channels/{channel} → {unlinked, channel, before, left}  the way out
    GET    /v1/bot/link         → {linked, channel, identifier, label, linked_at}   the bot asks
    POST   /v1/bot/link         → {linked, channel, identifier, label, linked_at}   the bot links
    POST   /v1/bot/unlink       → {unlinked, channel, reason}             the bot lets go
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, Request

from .identifiers import BadIdentifier
from .service import CHANNEL_CODE_TTL_S, Caller, Cloud, CloudError

# a code is a few characters in a JSON object; anything larger is not one
_BODY_BYTES = 8 * 1024


async def _body(request: Request) -> dict[str, Any]:
    """The body as a JSON object, small: this door takes a code, a channel and a chat id."""
    raw = await request.body()
    if len(raw) > _BODY_BYTES:
        raise CloudError(413, "too_large", "That request body is too large")
    try:
        data = json.loads(raw or b"{}")
    except ValueError as e:
        raise CloudError(400, "bad_request", "The body is not JSON") from e
    return data if isinstance(data, dict) else {}


def install(app, cloud: Cloud, caller_dep, bot_caller) -> None:
    """The account's two routes and the bot's two, `bot_caller` being the relay's own check
    of the bot's shared secret (it lives with the other bot doors)."""
    caller_auth = caller_dep
    router = APIRouter()

    @router.post("/v1/link/code")
    async def link_code(caller: Caller = Depends(caller_auth)) -> dict:
        """Six characters to type into the bot, with the account's own key.

        The account exists already (the person is signed in to the app) and the chat does
        not. One code at a time: asking again retires the one before it, so a code someone
        read off the screen stops working the moment a new one is made. Ten minutes, one
        use, and the alphabet has no O/0 or I/1 in it."""
        code, expires_at = await asyncio.to_thread(cloud.channel_code, caller)
        return {"code": code, "expires_at": expires_at, "expires_in_s": CHANNEL_CODE_TTL_S}

    @router.get("/v1/link/channels")
    async def link_channels(caller: Caller = Depends(caller_auth)) -> list[dict]:
        """The chats this account holds: the one it was made from, then every chat a code
        tied to it. An address that joined the account is in `/v1/me`'s `account.linked`."""
        return await asyncio.to_thread(cloud.channel_links, caller)

    @router.delete("/v1/link/channels/{channel}")
    async def unlink_channel(channel: str, caller: Caller = Depends(caller_auth)) -> dict:
        """Cut this account's chats of one messenger loose: the way out of a link.

        `channel` is `telegram` or `max`, and every chat a code tied to the account in that
        messenger is let go at once, so one call undoes what several codes joined. The bot
        there stops finding an account for those chats: the next message makes a fresh account
        of its own, with its own allowance, as on the first day.

        A chat that made the account is refused (`409 channel_is_the_account`): it is the
        account, not a row beside it, and deleting the account is the app's own door.
        Answers `{unlinked, channel, before, left}`; `unlinked: 0` is not an error, it says
        nothing was tied."""
        return await asyncio.to_thread(cloud.unlink_channel, caller, channel)

    @router.get("/v1/bot/link")
    async def bot_link_status(request: Request, channel: str = "telegram", chat_id: str = "", telegram_id: str = "") -> dict:
        """Whether this chat already belongs to an account (the bot asks before it reads a
        message as a code; a chat that is somebody's hears nothing about codes)."""
        bot_caller(request)
        chat = chat_id or telegram_id
        if not chat:
            raise CloudError(400, "bad_request", "Say which chat: chat_id")
        try:
            return await asyncio.to_thread(cloud.chat_link, channel, chat)
        except BadIdentifier as e:
            raise CloudError(400, "bad_identifier", "A chat is its numeric id") from e

    @router.post("/v1/bot/link")
    async def bot_link(request: Request) -> dict:
        """The chat types the code and becomes that account's chat.

        `identifier` is what the messenger calls the person there ("@name"), shown in the
        app; the relay cannot learn it on its own and an empty one is fine. A chat that
        already belongs to an account is refused (`link_taken`) rather than merged, and the
        code is not spent by that refusal."""
        bot_caller(request)
        body = await _body(request)
        channel = str(body.get("channel") or "telegram")[:16]
        chat = str(body.get("chat_id") or body.get("telegram_id") or "")
        code = str(body.get("code") or "")[:32]
        who = str(body.get("identifier") or "")[:80]
        try:
            return await asyncio.to_thread(cloud.link_channel, channel, chat, code, who)
        except BadIdentifier as e:
            raise CloudError(400, "bad_identifier", "A chat is its numeric id") from e

    @router.post("/v1/bot/unlink")
    async def bot_unlink(request: Request) -> dict:
        """The chat lets go of its account, asked for from inside the chat.

        The bot's half of `DELETE /v1/link/channels/{channel}`, and the only way out for
        somebody who has no app at all: a chat that made an account of its own is refused
        (`channel_is_the_account`) there too, because that is a deletion, and the answer says
        so in words the bot can pass on. `not_linked` is a plain answer, not a failure: the
        chat was a stranger already."""
        bot_caller(request)
        body = await _body(request)
        channel = str(body.get("channel") or "telegram")[:16]
        chat = str(body.get("chat_id") or body.get("telegram_id") or "")
        if not chat:
            raise CloudError(400, "bad_request", "Say which chat: chat_id")
        try:
            return await asyncio.to_thread(cloud.unlink_chat, channel, chat)
        except BadIdentifier as e:
            raise CloudError(400, "bad_identifier", "A chat is its numeric id") from e

    app.include_router(router)
