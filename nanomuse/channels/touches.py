"""Who writes to the bot — channel, sender, when, and how long the message was.

The words are never written: the question this answers is "was it me or someone else,
and from where", not what was said. One JSON line per inbound message, appended to the
bot\x27s workspace so it survives restarts and can be read by a report script.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


def _path() -> Path:
    return Path(os.environ.get("TOUCH_LOG") or "/workspace/touches.jsonl")


def note(
    channel: str, sender_id: object, chat_id: object, text: str = "", kind: str = "message"
) -> None:
    """Record one inbound message. Failure never breaks the chat: a lost line is a lost line."""
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": int(time.time()),
            "channel": str(channel or ""),
            "sender": str(sender_id or ""),
            "chat": str(chat_id or ""),
            "chars": len(text or ""),
            "kind": str(kind or "message"),
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass
