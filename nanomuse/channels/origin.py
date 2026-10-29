"""Which entrance a conversation belongs to: one messenger, or the account itself.

A Telegram chat and a MAX chat are two separate doors into the same account. A conversation
that began at one door is shown at that messenger, the web and the computer; it is not copied
into the other messenger. A conversation begun on the computer or in the web (and kept in the
cloud) is the account's own, so it may appear at both messengers as well.

The bot names its messenger-born threads ``channel-telegram-<id>`` and ``channel-max-<id>``;
the titles say the same for rows read from the relay. Everything else — ``main``, the side
chats of the computer and the web, the Feed — belongs to the account.
"""

from __future__ import annotations

_TELEGRAM_PREFIX = "channel-telegram-"
_MAX_PREFIX = "channel-max-"


def conversation_origin(thread_id: str, title: str = "") -> str:
    """``telegram``, ``max`` or ``account`` — never empty."""
    ident = str(thread_id or "")
    name = " ".join(str(title or "").split()).casefold()
    if ident.startswith(_MAX_PREFIX) or name.startswith(("max ·", "max·")):
        return "max"
    if ident.startswith(_TELEGRAM_PREFIX) or name.startswith(("telegram ·", "telegram·")):
        return "telegram"
    return "account"


def shows_in(origin: str, channel: str) -> bool:
    """Whether a conversation of this origin belongs in this messenger's list/chat."""
    return origin == "account" or origin == channel


__all__ = ["conversation_origin", "shows_in"]
