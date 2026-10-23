"""Explicit computer handoff, distinct from ordinary autonomous bot chat."""

from __future__ import annotations

from typing import Any


def computer_request(text: str) -> str | None:
    parts = text.strip().split(maxsplit=1)
    if parts and parts[0].lower().split("@")[0] == "/computer":
        return parts[1].strip() if len(parts) == 2 else ""
    return None


def select_computer(devices: list[dict[str, Any]], own_id: str) -> dict[str, Any] | None:
    eligible = [
        d
        for d in devices
        if d.get("id") != own_id
        and d.get("online")
        and d.get("kind") == "computer"
        and "task" in d.get("actions", [])
    ]
    return eligible[0] if len(eligible) == 1 else None
