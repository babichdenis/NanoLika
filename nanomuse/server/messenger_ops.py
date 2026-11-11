"""Durable, account-scoped admission of messenger operations.

This journal deliberately refuses to repeat an operation after the dispatch boundary if
its outcome is unknown. A model call or tool might have run before the process died.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


class OperationConflict(Exception):
    """The same operation id was reused for different input."""


class MessengerOperations:
    def __init__(self, path: Path, instance: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.instance = instance
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError:
            pass
        with closing(self._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS messenger_operations (
                account_id TEXT NOT NULL, operation_id TEXT NOT NULL, digest TEXT NOT NULL,
                thread_id TEXT NOT NULL, state TEXT NOT NULL, event_id TEXT NOT NULL DEFAULT '',
                instance TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (account_id, operation_id))""")
        os.chmod(path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA secure_delete=ON")
        return db

    @staticmethod
    def digest(payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()

    def reserve(self, account: str, operation: str, payload: dict[str, Any]) -> dict[str, str]:
        digest = self.digest(payload)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT digest, thread_id, state, event_id, instance FROM messenger_operations "
                "WHERE account_id=? AND operation_id=?", (account, operation),
            ).fetchone()
            if row is not None:
                if row["digest"] != digest:
                    raise OperationConflict("operation id already used for different input")
                state = row["state"] if row["instance"] == self.instance or row["state"] in {"queued", "rejected"} else "unknown"
                return {"state": state, "thread_id": row["thread_id"], "event_id": row["event_id"]}
            db.execute(
                "INSERT INTO messenger_operations(account_id, operation_id, digest, thread_id, state, instance, payload, event_id) "
                "VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)",
                (account, operation, digest, payload["thread_id"], self.instance,
                 json.dumps(payload, ensure_ascii=False, separators=(",", ":")), payload["user_event_id"]),
            )
            return {"state": "queued", "thread_id": payload["thread_id"], "event_id": payload["user_event_id"]}

    def claim(self, account: str, operation: str) -> bool:
        with closing(self._connect()) as db, db:
            updated = db.execute(
                "UPDATE messenger_operations SET state='dispatching', instance=? "
                "WHERE account_id=? AND operation_id=? AND state='queued'",
                (self.instance, account, operation),
            ).rowcount
        return updated == 1

    def accepted(self, account: str, operation: str, event_id: str) -> None:
        if not event_id:
            raise ValueError("event id required")
        with closing(self._connect()) as db, db:
            updated = db.execute(
                "UPDATE messenger_operations SET state='accepted', event_id=?, payload='{}' "
                "WHERE account_id=? AND operation_id=? AND state='dispatching' AND event_id=?",
                (event_id, account, operation, event_id),
            ).rowcount
            if updated != 1:
                raise RuntimeError("operation not dispatching")

    def reject_before_dispatch(self, account: str, operation: str) -> None:
        with closing(self._connect()) as db, db:
            db.execute("UPDATE messenger_operations SET state='rejected', payload='{}' WHERE account_id=? AND operation_id=? AND state='dispatching'", (account, operation))

    def lookup(self, account: str, operation: str) -> dict[str, str] | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT thread_id, state, event_id, instance FROM messenger_operations "
                "WHERE account_id=? AND operation_id=?", (account, operation),
            ).fetchone()
        if row is None:
            return None
        return {
            "thread_id": row["thread_id"], "event_id": row["event_id"],
            "state": row["state"] if row["instance"] == self.instance or row["state"] in {"queued", "rejected"} else "unknown",
        }


async def confirm_user_durable(thread: Any, event_id: str, mid: str, file_paths: list[str]) -> None:
    """Confirm the exact user event is on disk before acknowledging an operation.

    Timeline writes are normally deferred. Wait for an older in-flight write first so it
    cannot replace the newer snapshot after acknowledgement. A read-back is necessary:
    Timeline logs write errors rather than raising them.
    """
    import asyncio

    timeline = thread.timeline
    async with asyncio.timeout(10):
        while timeline._writing is not None and not timeline._writing.done():
            await asyncio.shield(timeline._writing)
    timeline.flush()
    path = timeline.path
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        raw = stream.read()
        os.fsync(stream.fileno())
    data = json.loads(raw)
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        raise ValueError("timeline unavailable")
    event = next((row for row in events if isinstance(row, dict) and row.get("id") == event_id), None)
    if not event or event.get("mid") != mid or event.get("type") != "user":
        raise ValueError("user event was not persisted")
    saved_files = [row.get("path") for row in event.get("files", []) if isinstance(row, dict)]
    if saved_files != file_paths:
        raise ValueError("attachment references were not persisted")
    # Persist the rename of the timeline snapshot, not just the file contents.
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
