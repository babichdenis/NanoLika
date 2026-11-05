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
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from .browser_auth import SESSION_COOKIE, browser_sign_in
from .identifiers import BadIdentifier
from .service import CHANNEL_CODE_TTL_S, Caller, Cloud, CloudError, chat_identity

# a code is a few characters in a JSON object; anything larger is not one
_BODY_BYTES = 8 * 1024


def _validate_telegram_init_data(init_data: str, bot_token: str) -> dict[str, Any] | None:
    """Verify the decoded Mini App query string, including its short launch lifetime.

    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    if not init_data or not bot_token or len(init_data) > _BODY_BYTES:
        return None
    try:
        if re.search(r"%(?![0-9a-fA-F]{2})", init_data):
            return None
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True, errors="strict", max_num_fields=32)
        params = dict(pairs)
        if len(params) != len(pairs):
            return None
        received_hash = params.pop("hash", "")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", received_hash):
            return None
        check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(received_hash.lower(), expected):
            return None
        age = time.time() - int(params.get("auth_date", "0"))
        if age < -30 or age > 300:
            return None
        user = json.loads(params.get("user", "null"))
        if not isinstance(user, dict) or type(user.get("id")) is not int or not 0 < user["id"] < 2**52:
            return None
        params["user"] = user
        return params
    except (ValueError, TypeError, UnicodeError):
        return None


def _validate_max_init_data(init_data: str, bot_token: str) -> dict[str, Any] | None:
    """Verify MAX's WebAppData start parameters, including the short launch lifetime.

    https://dev.max.ru/docs/webapps/validation — the secret is HMAC-SHA256 *keyed by the
    literal string* "WebAppData" over the bot token, and the launch parameters are joined
    with newlines; the hash is the hex digest over that string.
    """
    if not init_data or not bot_token or len(init_data) > _BODY_BYTES:
        return None
    try:
        if re.search(r"%(?![0-9a-fA-F]{2})", init_data):
            return None
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True, errors="strict", max_num_fields=32)
        params = dict(pairs)
        if len(params) != len(pairs):
            return None
        received_hash = params.pop("hash", "")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", received_hash):
            return None
        check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(received_hash.lower(), expected):
            return None
        age = time.time() - int(params.get("auth_date", "0"))
        if age < -30 or age > 300:
            return None
        user = json.loads(params.get("user", "null"))
        if not isinstance(user, dict) or type(user.get("id")) is not int or not 0 < user["id"] < 2**52:
            return None
        params["user"] = user
        return params
    except (ValueError, TypeError, UnicodeError):
        return None


def _max_session(cloud: Cloud, user_id: int) -> dict:
    """Issue only for an existing MAX identity; never register or merge accounts."""
    ident = chat_identity("max", str(user_id))
    with cloud.db._lock:
        alias = cloud._chat_alias("max", ident)
        account = (
            cloud.db.account(str(alias["account_id"])) if alias is not None else cloud.db.account_by_hashes(ident.hashes(cloud.s.hmac_key))
        )
        if account is None:
            raise CloudError(409, "max_not_linked", "Link MAX to your account first")
        if account["disabled"]:
            raise CloudError(403, "account_disabled", "This account is disabled")
        key, caller = cloud._issue_key(str(account["id"]), "MAX WebApp", "max_webapp", expires_at=int(time.time()) + 30 * 86400)
        return {"api_key": key, "account": {"id": caller.account_id, "hint": caller.hint}}


def _telegram_session(cloud: Cloud, user_id: int) -> dict:
    """The Telegram id and its mini app are one account, until an address joins them.

    The chat makes the account on its first message (`/v1/bot/signin`), and the mini app
    opens that very account: no address, no code, no second allowance. An address typed
    later (`/connect`, `link_into`) joins the two ways in, and from then on the account is
    the one every device signs in to. An account that already holds this Telegram (a code
    linked it to an e-mail account, or MAX is there too) is found through its alias first,
    so the mini app never quietly makes a second account beside it.
    """
    ident = chat_identity("telegram", str(user_id))
    # The database lock also protects alias unlinking. No nested transaction is needed.
    with cloud.db._lock:
        alias = cloud._chat_alias("telegram", ident)
        account = (
            cloud.db.account(str(alias["account_id"])) if alias is not None else cloud.db.account_by_hashes(ident.hashes(cloud.s.hmac_key))
        )
    if account is None:
        # No account yet: the first visit from the mini app makes the same account the
        # first message to the bot makes, and the key is issued to it.
        key, caller, _created = cloud.sign_in_bot(str(user_id), "Telegram WebApp", channel="telegram")
        return {"api_key": key, "account": {"id": caller.account_id, "hint": caller.hint}}
    if account["disabled"]:
        raise CloudError(403, "account_disabled", "This account is disabled")
    key, caller = cloud._issue_key(str(account["id"]), "Telegram WebApp", "telegram_webapp", expires_at=int(time.time()) + 30 * 86400)
    return {"api_key": key, "account": {"id": caller.account_id, "hint": caller.hint}}


LOGIN_TTL_S = 300
_LOGIN_CODE = re.compile(r"nl_LG[A-F0-9]{30}", re.IGNORECASE)
_OPAQUE = re.compile(r"[A-Za-z0-9_-]{43,128}")
_VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")


class TelegramLogins:
    """One-time tickets for "sign in on this computer with Telegram".

    The computer makes a PKCE challenge and shows the code as a QR; the person opens the
    bot with it; the bot tells the relay which account the chat is (the same account the
    chat already has, or the one its first message makes); the computer polls with its
    verifier and gets a key. The bot never sees the key, and the relay never sees the
    verifier.
    """

    def __init__(self, cloud: Cloud):
        self.cloud = cloud
        with cloud.db.tx() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS telegram_logins ("
                "code_hash TEXT PRIMARY KEY, challenge TEXT NOT NULL, account_id TEXT NOT NULL DEFAULT '', "
                "created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL)"
            )

    @staticmethod
    def _hash(code: str) -> str:
        return hashlib.sha256(code.encode()).hexdigest()

    def start(self, challenge: str) -> tuple[str, int]:
        if not _OPAQUE.fullmatch(challenge or ""):
            raise CloudError(400, "bad_request", "A sign-in challenge is required")
        now = int(time.time())
        # Upper from the start: the bot gives the code back uppercased, and the hash must match.
        code = "NL_LG" + secrets.token_hex(15).upper()
        with self.cloud.db.tx() as connection:
            connection.execute("DELETE FROM telegram_logins WHERE expires_at<=?", (now,))
            connection.execute(
                "INSERT INTO telegram_logins(code_hash,challenge,account_id,created_at,expires_at) VALUES (?,?,?,?,?)",
                (self._hash(code), challenge, "", now, now + LOGIN_TTL_S),
            )
        return code, now + LOGIN_TTL_S

    def approve(self, code: str, chat_id: str) -> dict[str, Any]:
        """The bot's half: the chat names the account the computer will sign in to."""
        code = (code or "").strip().upper()
        if not _LOGIN_CODE.fullmatch(code):
            raise CloudError(400, "bad_request", "That is not a sign-in code")
        row = None
        with self.cloud.db.tx() as connection:
            row = connection.execute(
                "SELECT * FROM telegram_logins WHERE code_hash=?", (self._hash(code),)
            ).fetchone()
        if row is None or int(row["expires_at"]) <= int(time.time()):
            raise CloudError(400, "login_expired", "This sign-in code has expired; make a new one on the computer")
        key, caller, _created = self.cloud.sign_in_bot(str(chat_id), "Telegram Desktop", channel="telegram")
        # The bot does not keep a key: the computer gets its own in `poll`.
        self.cloud.db.revoke_key(hashlib.sha256(key.encode()).hexdigest())
        with self.cloud.db.tx() as connection:
            connection.execute(
                "UPDATE telegram_logins SET account_id=? WHERE code_hash=?",
                (caller.account_id, self._hash(code)),
            )
        return {"ok": True, "account": {"id": caller.account_id, "hint": caller.hint}}

    def poll(self, code: str, verifier: str) -> dict[str, Any] | None:
        """The computer's half: None while the bot has not approved, the key when it has."""
        code = (code or "").strip().upper()
        if not _LOGIN_CODE.fullmatch(code) or not _VERIFIER.fullmatch(verifier or ""):
            raise CloudError(400, "bad_request", "A sign-in code and its proof are required")
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        with self.cloud.db.tx() as connection:
            row = connection.execute(
                "SELECT * FROM telegram_logins WHERE code_hash=?", (self._hash(code),)
            ).fetchone()
        if row is None or int(row["expires_at"]) <= int(time.time()) or not hmac.compare_digest(str(row["challenge"]), expected):
            raise CloudError(400, "login_expired", "This sign-in has expired; start again on the computer")
        account_id = str(row["account_id"] or "")
        if not account_id:
            return None
        with self.cloud.db.tx() as connection:
            connection.execute("DELETE FROM telegram_logins WHERE code_hash=?", (self._hash(code),))
        account = self.cloud.db.account(account_id)
        if account is None or account["disabled"]:
            raise CloudError(403, "account_disabled", "This account is disabled")
        key, caller = self.cloud._issue_key(
            account_id, "Telegram Desktop", "telegram_desktop", expires_at=int(time.time()) + 90 * 86400
        )
        return {"api_key": key, "created": False, "account": {"id": caller.account_id, "hint": caller.hint}}


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
    caller_auth_dependency = Depends(caller_auth)
    router = APIRouter()
    logins = TelegramLogins(cloud)

    @router.post("/v1/auth/telegram/start")
    async def telegram_login_start(request: Request) -> dict:
        """A code for the computer's QR: the person opens the bot, the computer gets a key."""
        body = await _body(request)
        code, expires_at = await asyncio.to_thread(logins.start, str(body.get("challenge") or ""))
        return {
            "code": code,
            "url": f"https://t.me/nanolika_bot?start={code}",
            "expires_at": expires_at,
            "expires_in_s": LOGIN_TTL_S,
        }

    @router.post("/v1/auth/telegram/poll")
    async def telegram_login_poll(request: Request) -> Response:
        body = await _body(request)
        answer = await asyncio.to_thread(
            logins.poll, str(body.get("code") or ""), str(body.get("verifier") or "")
        )
        if answer is None:
            return JSONResponse({"state": "pending"}, status_code=202)
        return JSONResponse(answer)

    @router.post("/v1/bot/telegram-login")
    async def bot_telegram_login(request: Request) -> dict:
        bot_caller(request)
        body = await _body(request)
        return await asyncio.to_thread(
            logins.approve, str(body.get("code") or ""), str(body.get("chat_id") or "")
        )

    @router.post("/v1/link/code")
    async def link_code(caller: Caller = Depends(caller_auth)) -> dict:
        """Six characters to type into the bot, with the account's own key.

        The account exists already (the person is signed in to the app) and the chat does
        not. One code at a time: asking again retires the one before it, so a code someone
        read off the screen stops working the moment a new one is made. Ten minutes, one
        use, and the alphabet has no O/0 or I/1 in it."""
        code, expires_at = await asyncio.to_thread(cloud.channel_code, caller)
        return {"code": code, "expires_at": expires_at, "expires_in_s": CHANNEL_CODE_TTL_S}

    @router.delete("/v1/link/devices/{device_id}", status_code=204)
    async def disconnect_device(device_id: str, caller: Caller = caller_auth_dependency) -> Response:
        hub = getattr(app.state, "hub", None)
        if hub is None:
            raise CloudError(503, "hub_unavailable", "Device connections are unavailable")
        await hub.disconnect_device(caller, device_id)
        return Response(status_code=204)

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

    @router.post("/v1/auth/telegram-webapp")
    async def telegram_webapp_sign_in(request: Request, response: Response) -> dict:
        public = urlsplit(cloud.s.public_base)
        expected_origin = f"{public.scheme}://{public.netloc}"
        if request.headers.get("origin") != expected_origin:
            raise CloudError(403, "bad_origin", "Open the WebApp on the cloud origin")
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise CloudError(415, "bad_content_type", "Send JSON")
        if not cloud.s.telegram_bot_token:
            raise CloudError(503, "webapp_check_unconfigured", "Telegram sign-in is unavailable")
        body = await _body(request)
        raw = body.get("init_data")
        validated = _validate_telegram_init_data(raw, cloud.s.telegram_bot_token) if isinstance(raw, str) else None
        if validated is None:
            raise CloudError(401, "invalid_init_data", "Reopen the Mini App from Telegram")
        result = await asyncio.to_thread(_telegram_session, cloud, validated["user"]["id"])
        response.headers["Cache-Control"] = "private, no-store"
        caller = await asyncio.to_thread(cloud.authenticate, result["api_key"])
        browser_sign_in(response, cloud, caller, previous_token=request.cookies.get(SESSION_COOKIE, ""))
        return result

    @router.post("/v1/auth/max-webapp")
    async def max_webapp_sign_in(request: Request, response: Response) -> dict:
        public = urlsplit(cloud.s.public_base)
        expected_origin = f"{public.scheme}://{public.netloc}"
        if request.headers.get("origin") != expected_origin:
            raise CloudError(403, "bad_origin", "Open the mini app on the cloud origin")
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise CloudError(415, "bad_content_type", "Send JSON")
        if not cloud.s.max_bot_token:
            raise CloudError(503, "webapp_check_unconfigured", "MAX sign-in is unavailable")
        body = await _body(request)
        raw = body.get("init_data")
        validated = _validate_max_init_data(raw, cloud.s.max_bot_token) if isinstance(raw, str) else None
        if validated is None:
            raise CloudError(401, "invalid_init_data", "Reopen the mini app from MAX")
        result = await asyncio.to_thread(_max_session, cloud, validated["user"]["id"])
        response.headers["Cache-Control"] = "private, no-store"
        caller = await asyncio.to_thread(cloud.authenticate, result["api_key"])
        browser_sign_in(response, cloud, caller, previous_token=request.cookies.get(SESSION_COOKIE, ""))
        return result

    @router.post("/v1/link/check-webapp")
    async def check_webapp_link(request: Request) -> dict:
        """Check if a Telegram WebApp user is linked to an account.

        Accepts {init_data: string} from the WebApp, validates the HMAC using the bot token,
        extracts the user's chat_id, and returns whether that chat is linked to an account.
        Does NOT require the user to be signed in to the app — works for unauthenticated WebApp opens.
        """
        if not cloud.s.telegram_bot_token:
            raise CloudError(503, "webapp_check_unconfigured", "Telegram bot token not configured")

        body = await _body(request)
        init_data = str(body.get("init_data") or "")

        if not init_data:
            raise CloudError(400, "bad_request", "init_data is required")

        # Validate initData and extract user
        validated = _validate_telegram_init_data(init_data, cloud.s.telegram_bot_token)
        if not validated:
            raise CloudError(401, "invalid_init_data", "Invalid or tampered init_data")

        user = validated.get("user")
        if not user or not isinstance(user, dict) or "id" not in user:
            raise CloudError(400, "bad_request", "init_data must contain user.id")

        chat_id = str(user["id"])

        # Check if this Telegram chat is linked to an account
        try:
            link_info = await asyncio.to_thread(cloud.chat_link, "telegram", chat_id)
            return {
                "linked": link_info.get("linked", False),
                "channel": "telegram",
                "chat_id": chat_id,
                "identifier": link_info.get("identifier", ""),
                "label": link_info.get("label", ""),
                "linked_at": link_info.get("linked_at"),
                "own_account": link_info.get("own", False),
            }
        except BadIdentifier as e:
            raise CloudError(400, "bad_identifier", "Invalid chat id") from e

    app.include_router(router)
