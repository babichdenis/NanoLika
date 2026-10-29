"""The short code that joins a chat to the account its person already signed in on.

The app shows six characters (`POST /v1/link/code`, under the account's key). The person
sends them to the bot, and this module is the bot's half: it asks the relay whether the
chat already belongs to an account and, when it does not, hands the code over
(`POST /v1/bot/link`, proved by the bot's shared secret). The relay writes the row; nothing
is stored here, and the words the person gets back are this module's only output.

The two settings are the ones the bot's own sign-in door already uses: ``cloud_url`` (the
relay) and ``cloud_bot_token`` (its shared secret). Without them the chat sees nothing
about codes and its messages stay ordinary messages. Only a message that is nothing but a
code is read as one, and only in a chat that has no account yet: every command, every word
and every question is left alone.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from nanomuse.channels.bot_copy import channel_text
from nanomuse.logger import logger

# no O/0 and no I/1: the app shows the code and the person types it on a phone
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6
_LIKE_A_CODE = re.compile(rf"^[{CODE_ALPHABET}]{{{CODE_LENGTH}}}$")
# a status question and a link are both answered in a moment; a slower relay is not worth
# holding a chat turn for
TIMEOUT_S = 15.0


def as_code(text: str) -> str:
    """The code a message is, in the form the relay takes, or "" when it is not one.

    A person types the six characters in whatever case their phone offers; anything else
    (a command, a word, a sentence with a code inside it) is not a code."""
    written = (text or "").strip().upper()
    return written if _LIKE_A_CODE.match(written) else ""


class ChatLink:
    """One channel's way to the relay's two linking calls (``GET`` and ``POST /v1/bot/link``).

    The messenger is this bot's own name (``telegram`` or ``max``) and goes with every call:
    the relay keys a chat's account on the pair, because the two messengers number their
    users in separate spaces and the same number there is two people. The id is sent as
    ``chat_id``, never as ``telegram_id``: that name is the older bots' spelling, and a MAX
    chat is not a Telegram one."""

    def __init__(
        self,
        channel: str,
        settings: dict[str, Any],
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.channel = (channel or "").strip().lower() or "telegram"
        self._settings = settings
        self._transport = transport
        # The last refusal that was a question rather than an error: the chat is connected to
        # an account (`label`, masked) and the person may want to leave it. Set by `redeem`,
        # read by the manager, cleared by the next successful link.
        self.taken: dict[str, Any] = {}

    def configured(self) -> bool:
        """Is there a relay to ask at all (the same pair the bot's sign-in uses)?"""
        return bool(self._base()) and bool(self._token())

    def _base(self) -> str:
        return str(self._settings.get("cloud_url") or "").strip().rstrip("/")

    def _token(self) -> str:
        return str(self._settings.get("cloud_bot_token") or "").strip()

    def _client(self, base: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=base,
            timeout=TIMEOUT_S,
            headers={"X-Bot-Token": self._token()},
            transport=self._transport,
        )

    async def status(self, chat_id: str) -> dict[str, Any] | None:
        """What the relay says about this chat, or None when it could not be asked."""
        base = self._base()
        if not base:
            return None
        try:
            async with self._client(base) as client:
                answer = await client.get(
                    "/v1/bot/link", params={"channel": self.channel, "chat_id": chat_id}
                )
        except httpx.HTTPError as exc:
            logger.warning("{}: the relay could not be asked about a chat: {}", self.channel, exc)
            return None
        if answer.status_code >= 400:
            logger.warning(
                "{}: the relay refused the link status ({})", self.channel, answer.status_code
            )
            return None
        try:
            data = answer.json()
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    async def redeem(
        self, chat_id: str, code: str, identifier: str = "", language: str = ""
    ) -> str:
        """Link the chat with the code and say how it went. Never raises: a person who typed
        a code gets words, not a broken chat."""
        base = self._base()
        if not base:
            return ""
        body = {"channel": self.channel, "chat_id": chat_id, "code": code, "identifier": identifier}
        try:
            async with self._client(base) as client:
                answer = await client.post("/v1/bot/link", json=body)
        except httpx.HTTPError as exc:
            logger.warning("{}: the relay could not be told about a code: {}", self.channel, exc)
            return channel_text(language, "link_offline")
        try:
            data = answer.json()
        except ValueError:
            data = {}
        if answer.status_code == 200:
            self.taken = {}
            return channel_text(language, "link_linked")
        said = data.get("error") if isinstance(data, dict) else None
        error: dict[str, Any] = said if isinstance(said, dict) else {}
        code_name = str(error.get("code") or "")
        if code_name in ("link_wrong", "code_expired"):
            return channel_text(language, "link_wrong")
        if code_name == "link_taken":
            # The chat is somebody's already. The relay says whose (a masked address) and
            # whether that chat *is* the account, because the answer is a question rather than
            # a wall: "connected to this account, leave it and connect here instead?".
            raw_taken = error.get("taken")
            taken = raw_taken if isinstance(raw_taken, dict) else {}
            self.taken = {
                "label": str(taken.get("label") or ""),
                "own": bool(taken.get("own")),
                "code": code,
            }
            if not self.taken["label"] and not self.taken["own"]:
                # The relay refused without saying whose the chat is (an older relay, or a
                # refusal from another door). There is nothing to ask about, so this stays the
                # plain sentence it always was: a question with no account in it would confuse
                # more than it helps.
                self.taken = {}
            return channel_text(language, "link_taken")
        if code_name == "link_off":
            # the operator switched linking off: the code is silence, not an error
            return ""
        logger.warning(
            "{}: the relay refused a code ({}: {})", self.channel, answer.status_code, code_name
        )
        return channel_text(language, "link_offline")

    async def release(self, chat_id: str, language: str = "") -> str:
        """Let this chat go from its account and say how it went. Never raises.

        The way out of a link, from inside the chat: a person with no app at all is exactly
        who needs it, so it must end in words rather than an exception. The relay's own
        refusals are named: a chat that made the account cannot be cut loose (that is a
        deletion, and it belongs in the app), and a chat that was a stranger already is told
        so plainly instead of being handed an error."""
        base = self._base()
        if not base:
            return channel_text(language, "unlink_offline")
        body = {"channel": self.channel, "chat_id": chat_id}
        try:
            async with self._client(base) as client:
                answer = await client.post("/v1/bot/unlink", json=body)
        except httpx.HTTPError as exc:
            logger.warning("{}: the relay could not be told to unlink: {}", self.channel, exc)
            return channel_text(language, "unlink_offline")
        try:
            data = answer.json()
        except ValueError:
            data = {}
        if answer.status_code >= 400:
            logger.warning("{}: the relay refused an unlink ({})", self.channel, answer.status_code)
            return channel_text(language, "unlink_offline")
        said: dict[str, Any] = data if isinstance(data, dict) else {}
        reason = str(said.get("reason") or "")
        if said.get("unlinked"):
            return channel_text(language, "unlink_done")
        if reason in ("channel_is_the_account",):
            return channel_text(language, "unlink_is_account")
        if reason == "account_gone":
            return channel_text(language, "unlink_done")
        return channel_text(language, "unlink_not_linked")


async def answer_code(
    link: ChatLink, chat_id: str, text: str, identifier: str = "", language: str = ""
) -> str:
    """The words to send back when `text` is a code.

    "" when the message is not a code at all, or when the relay has no opinion: the caller then
    passes the message on, exactly as before.

    A chat that already belongs to an account used to be skipped here — its code was never even
    asked about, which left a person holding a fresh QR with nowhere to put it. It is asked
    about now: the relay answers `link_taken` with the account the chat belongs to, and the
    manager turns that into the question "leave that account and connect here instead?"."""
    code = as_code(text)
    if not code or not link.configured():
        return ""
    link.taken = {}
    answer = await link.redeem(chat_id, code, identifier, language)
    if not link.taken:
        return answer
    # The chat belongs to an account already: a question, not a refusal, and not a dead end.
    # The caller sends these two buttons instead of the plain text.
    label = link.taken.get("label") or channel_text(language, "link_switch_unknown")
    if link.taken.get("own"):
        return channel_text(language, "link_switch_own")
    return channel_text(language, "link_switch_ask").replace("{label}", label)


__all__ = ["CODE_ALPHABET", "CODE_LENGTH", "ChatLink", "answer_code", "as_code"]
