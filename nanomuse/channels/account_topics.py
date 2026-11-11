"""Native topic creation without restoring archived chats or repeating unknown requests."""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Awaitable, Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from nanomuse.channels.account_picker import AccountChatPicker


class AccountTopics:
    def __init__(self, path: Path, picker: AccountChatPicker):
        self.path = path
        self.picker = picker
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError:
            pass
        with closing(self._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS topics (
                account TEXT NOT NULL, sender TEXT NOT NULL, chat TEXT NOT NULL,
                cid TEXT NOT NULL, topic INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL,
                PRIMARY KEY (account, sender, chat, cid))""")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS topic_destination_unique "
                       "ON topics(account, sender, chat, topic) WHERE topic > 0")
            db.execute("""CREATE TABLE IF NOT EXISTS topic_enrollments (
                account TEXT NOT NULL, sender TEXT NOT NULL, chat TEXT NOT NULL,
                started_at REAL NOT NULL, PRIMARY KEY (account, sender, chat))""")
        os.chmod(path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def enroll(self, account: str, sender: str, chat: str) -> float:
        """Remember when automatic discovery began; do not recreate unknown old topics."""
        self.picker._identity(account, "telegram", sender, chat)
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO topic_enrollments(account,sender,chat,started_at) VALUES(?,?,?,?)",
                (account, sender, chat, time.time()),
            )
            row = db.execute(
                "SELECT started_at FROM topic_enrollments WHERE account=? AND sender=? AND chat=?",
                (account, sender, chat),
            ).fetchone()
        assert row is not None
        return float(row[0])

    def _reserve(self, account: str, sender: str, chat: str, cid: str) -> bool:
        with closing(self._connect()) as db, db:
            return bool(db.execute(
                "INSERT OR IGNORE INTO topics(account,sender,chat,cid,state) VALUES(?,?,?,?,'creating')",
                (account, sender, chat, cid),
            ).rowcount)

    def _finish(self, account: str, sender: str, chat: str, cid: str, topic: int) -> None:
        with closing(self._connect()) as db, db:
            if db.execute(
                "UPDATE topics SET state=?, topic=? WHERE account=? AND sender=? AND chat=? AND cid=? AND state='creating'",
                ("active" if topic > 0 else "unknown", topic, account, sender, chat, cid),
            ).rowcount != 1:
                raise RuntimeError("topic reservation lost")

    def register(
        self, account: str, sender: str, chat: str, cid: str, topic: int, catalog: dict[str, Any],
        *, retired: bool = False,
    ) -> None:
        """Adopt a proven existing topic, including a retired one, without recreating it."""
        self.picker._identity(account, "telegram", sender, chat)
        active = self.picker._active(account, catalog)
        if not cid or topic <= 0 or (not retired and cid not in active):
            raise ValueError("verified conversation and topic required")
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            held = db.execute(
                "SELECT cid,topic,state FROM topics WHERE account=? AND sender=? AND chat=? AND (cid=? OR topic=?)",
                (account, sender, chat, cid, topic),
            ).fetchall()
            if any(row["cid"] != cid or row["topic"] != topic for row in held):
                raise ValueError("topic already belongs to another conversation")
            if held and held[0]["state"] == "retired" and not retired:
                raise ValueError("retired topic requires explicit restoration")
            db.execute(
                "INSERT OR IGNORE INTO topics(account,sender,chat,cid,topic,state) VALUES(?,?,?,?,?,?)",
                (account, sender, chat, cid, topic, "retired" if retired else "active"),
            )

    def existing(self, account: str, sender: str, chat: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT cid, topic, state FROM topics WHERE account=? AND sender=? AND chat=?",
                (account, sender, chat),
            ).fetchall()
        return [dict(row) for row in rows]

    async def reconcile(
        self, account: str, sender: str, chat: str, catalog: dict[str, Any],
        create_topic: Callable[[str], Awaitable[int]],
        topic_chat: Callable[[str, int], str],
    ) -> list[dict[str, Any]]:
        """Create only previously unseen active topics. Timeout is permanently unknown.

        A deleted/closed topic remains recorded; this method never recreates it. The bot
        can explicitly repair an unknown or deleted mapping after user confirmation.
        """
        self.picker._identity(account, "telegram", sender, chat)
        active = self.picker._active(account, catalog)
        for row in catalog["conversations"]:
            if not isinstance(row, dict) or str(row.get("cid") or "") not in active:
                continue
            cid = str(row["cid"])
            if not self._reserve(account, sender, chat, cid):
                continue
            try:
                topic = int(await create_topic(str(row.get("title") or "Беседа")[:100]))
            except Exception:  # noqa: BLE001 - Telegram may have created it before timeout
                self._finish(account, sender, chat, cid, 0)
                continue
            self._finish(account, sender, chat, cid, topic)
        # A created topic can be rebound after a process restart, but only while its
        # account's fresh catalogue still explicitly lists the conversation as active.
        for row in self.existing(account, sender, chat):
            if row["state"] == "active" and row["cid"] in active and row["topic"] > 0:
                self.picker.choose(account, "telegram", sender, topic_chat(chat, row["topic"]), row["cid"], catalog)
        return self.existing(account, sender, chat)
