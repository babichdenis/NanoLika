"""Resolve explicit connection start links before pairing, chat menus or model turns."""

from __future__ import annotations

import re
from collections.abc import Callable

from nanomuse.channels.base import Channel, InboundMessage
from nanomuse.channels.bot_copy import channel_text

_START = re.compile(r"^/start(?:@[A-Za-z0-9_]+)?\s+nl_((?:TG|MX)[A-F0-9]{30})$", re.I)
_PREFIXES = {"telegram": "TG", "max": "MX"}


def start_token(text: str, channel: str) -> str:
    match = _START.fullmatch((text or "").strip())
    if not match:
        return ""
    token = match.group(1).upper()
    return token if token.startswith(_PREFIXES.get(channel, "!")) else ""


async def handle_start_link(
    channel: Channel,
    msg: InboundMessage,
    language: str,
    on_linked: Callable[[], None] | None = None,
) -> bool:
    # Consume even a wrong-channel nl_ link, so its secret never becomes a model prompt.
    if not re.match(r"^/start(?:@[A-Za-z0-9_]+)?\s+nl_", msg.text.strip(), re.I):
        return False
    if msg.is_group:
        return True
    token = start_token(msg.text, msg.channel)
    factory = getattr(channel, "_link", None)
    if not token:
        answer = channel_text(language, "link_wrong")
    elif factory is None or not factory().configured():
        answer = channel_text(language, "link_offline")
    else:
        try:
            answer = await factory().redeem(msg.sender_id, token, msg.sender_name[:80], language)
        except Exception:  # noqa: BLE001  # a connection failure must not forward the secret to the model
            answer = channel_text(language, "link_offline")
    # Admission to the bot follows a successful account link; device/tool permissions stay unchanged.
    if on_linked is not None and answer == channel_text(language, "link_linked"):
        on_linked()
    await channel.send(msg.chat_id, answer or channel_text(language, "link_offline"))
    return True
