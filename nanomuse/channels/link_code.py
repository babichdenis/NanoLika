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
    """One channel's way to the relay's two linking calls (``GET`` and ``POST /v1/bot/link``)."""

    def __init__(
        self,
        channel: str,
        settings: dict[str, Any],
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.channel = channel
        self._settings = settings
        self._transport = transport

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
            return channel_text(language, "link_linked")
        said = data.get("error") if isinstance(data, dict) else None
        error: dict[str, Any] = said if isinstance(said, dict) else {}
        code_name = str(error.get("code") or "")
        if code_name in ("link_wrong", "code_expired"):
            return channel_text(language, "link_wrong")
        if code_name == "link_taken":
            return channel_text(language, "link_taken")
        if code_name == "link_off":
            # the operator switched linking off: the code is silence, not an error
            return ""
        logger.warning(
            "{}: the relay refused a code ({}: {})", self.channel, answer.status_code, code_name
        )
        return channel_text(language, "link_offline")


async def answer_code(
    link: ChatLink, chat_id: str, text: str, identifier: str = "", language: str = ""
) -> str:
    """The words to send back when `text` is a code for a chat that has no account yet.

    "" when the message is not a code, when the chat already belongs to an account (its
    messages are ordinary messages, code or not), or when the relay has no opinion: the
    caller then passes the message on, exactly as before."""
    code = as_code(text)
    if not code or not link.configured():
        return ""
    known = await link.status(chat_id)
    if known is None or known.get("linked"):
        return ""
    return await link.redeem(chat_id, code, identifier, language)


__all__ = ["CODE_ALPHABET", "CODE_LENGTH", "ChatLink", "answer_code", "as_code"]
