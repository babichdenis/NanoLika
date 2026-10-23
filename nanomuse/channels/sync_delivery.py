"""Bounded delivery receipts, without storing message text or files."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path


class SyncDeliveryReceipts:
    def __init__(self, path: Path, limit: int = 8192):
        self.path = path
        self.limit = limit
        self.enabled_at = time.time()
        self.keys: list[str] = []
        try:
            saved = json.loads(path.read_text("utf-8"))
            started = saved["enabled_at"]
            keys = saved["delivered"]
            if not isinstance(started, (int, float)) or not isinstance(keys, list):
                raise ValueError("invalid delivery receipts")
            self.enabled_at = float(started)
            self.keys = [k for k in keys if isinstance(k, str) and len(k) == 64][-limit:]
        except (OSError, ValueError, TypeError, KeyError):
            self._save()
        self.seen = set(self.keys)

    def eligible(self, timestamp: str) -> bool:
        try:
            return (
                datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
                >= self.enabled_at
            )
        except (ValueError, TypeError, AttributeError, OverflowError):
            return False

    @staticmethod
    def key(channel: str, chat: str, mid: str, role: str) -> str:
        return hashlib.sha256(json.dumps([channel, chat, mid, role]).encode()).hexdigest()

    def delivered(self, key: str) -> bool:
        return key in self.seen

    def mark(self, key: str) -> None:
        if key in self.seen:
            return
        self.keys = (self.keys + [key])[-self.limit :]
        self.seen = set(self.keys)
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        # Create private before writing, not after exposing a newly created file.
        import os

        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"enabled_at": self.enabled_at, "delivered": self.keys}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        tmp.replace(self.path)
