"""Account-scoped messenger selections backed by the live cloud catalogue.

A local selection is only a hint. Every use checks a complete catalogue obtained for the
same verified account. Missing/archived chats and disabled sync are never reopened.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


class CatalogUnavailable(ValueError):
    """No complete trusted catalogue is available; preserve the saved selection."""


class ConversationUnavailable(ValueError):
    """The selected conversation is absent or archived."""


class AccountChatPicker:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError:
            pass
        with closing(self._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS selections (
                account TEXT NOT NULL, channel TEXT NOT NULL, sender TEXT NOT NULL,
                chat TEXT NOT NULL, conversation TEXT NOT NULL,
                PRIMARY KEY(account, channel, sender, chat))""")
        os.chmod(path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5)

    @staticmethod
    def _active(account: str, catalog: dict[str, Any]) -> set[str]:
        if not account or not isinstance(catalog, dict) or catalog.get("account_id") != account:
            raise CatalogUnavailable("account catalogue unavailable")
        if catalog.get("complete") is not True or not isinstance(catalog.get("conversations"), list):
            raise CatalogUnavailable("account catalogue incomplete")
        return {
            str(row["cid"]) for row in catalog["conversations"]
            if isinstance(row, dict) and row.get("cid") and not row.get("archived") and not row.get("deleted")
        }

    @staticmethod
    def _identity(account: str, channel: str, sender: str, chat: str) -> tuple[str, str, str, str]:
        if not all((account, sender, chat)) or channel not in {"telegram", "max"}:
            raise ValueError("verified account and private channel identity required")
        return account, channel, sender, chat

    def choose(
        self, account: str, channel: str, sender: str, chat: str, cid: str, catalog: dict[str, Any],
    ) -> None:
        identity = self._identity(account, channel, sender, chat)
        if cid not in self._active(account, catalog):
            raise ConversationUnavailable("conversation unavailable")
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO selections(account, channel, sender, chat, conversation) VALUES(?, ?, ?, ?, ?) "
                "ON CONFLICT(account, channel, sender, chat) DO UPDATE SET conversation=excluded.conversation",
                (*identity, cid),
            )

    def selected(
        self, account: str, channel: str, sender: str, chat: str, catalog: dict[str, Any],
    ) -> str:
        identity = self._identity(account, channel, sender, chat)
        active = self._active(account, catalog)
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT conversation FROM selections WHERE account=? AND channel=? AND sender=? AND chat=?",
                identity,
            ).fetchone()
        if row is None:
            return ""
        if row[0] not in active:
            raise ConversationUnavailable("conversation unavailable")
        return str(row[0])
