"""Operator-provided welcome artwork, never user attachments or model output."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def artwork_settings() -> dict[str, Any]:
    path = os.environ.get("NANOLIKA_WELCOME_CONFIG", "")
    if not path:
        return {}
    try:
        data = json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


async def send_welcome_art(channel: Any, chat_id: str) -> bool:
    """Return false when no artwork is configured; errors fall back at the caller."""
    settings = artwork_settings()
    if channel.name == "telegram":
        photo = settings.get("telegram_photo")
        if not isinstance(photo, str) or not photo:
            return False
        body = {"chat_id": chat_id, "photo": photo, "disable_notification": True}
        connection = str(channel.settings.get("business_connection_id") or "")
        if connection:
            body["business_connection_id"] = connection
        await channel._call("sendPhoto", body)
        return True
    if channel.name == "max":
        token = settings.get("max_image_token")
        url = settings.get("max_image_url")
        if isinstance(token, str) and token:
            payload = {"token": token}
        elif isinstance(url, str) and url.startswith("https://"):
            payload = {"url": url}
        else:
            return False
        await channel._call(
            "POST",
            "/messages",
            params={"chat_id": chat_id},
            json={
                "text": "",
                "notify": False,
                "attachments": [{"type": "image", "payload": payload}],
            },
        )
        return True
    return False
