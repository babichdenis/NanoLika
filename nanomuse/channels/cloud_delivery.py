"""Durable delivery journal for one shared messenger bridge.

A part is reserved before its single platform request. Once a request could have
reached Telegram or MAX, a crash or exception makes its result ambiguous: it is
never sent again automatically. All senders must use this same journal; this is
not a cross-device arbiter and does not replace the relay's delivery protocol.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
from collections.abc import Awaitable, Callable, Sequence
from contextlib import closing
from pathlib import Path

MAX_ACCOUNT_DELIVERIES = 10_000
MAX_ACCOUNT_PARTS = 50_000
MAX_DECISIONS_PER_DELIVERY = 32


class DeliveryFull(Exception):
    """The bounded delivery history cannot accept another record."""


class DeliveryConflict(Exception):
    """An operation was reused with a different destination or message."""


class DeliveryDeferred(Exception):
    """The relay explicitly refused a claim before any platform request was made.

    Never raise this after starting a platform request or an ambiguous relay timeout.
    """


class CloudDelivery:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError:
            pass
        with closing(self._connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS cloud_deliveries (
                    account TEXT NOT NULL, channel TEXT NOT NULL, operation TEXT NOT NULL,
                    digest TEXT NOT NULL, parts INTEGER NOT NULL,
                    PRIMARY KEY (account, channel, operation)
                );
                CREATE TABLE IF NOT EXISTS cloud_delivery_parts (
                    account TEXT NOT NULL, channel TEXT NOT NULL, operation TEXT NOT NULL,
                    part INTEGER NOT NULL, state TEXT NOT NULL, claim TEXT NOT NULL DEFAULT '',
                    remote_id TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (account, channel, operation, part),
                    FOREIGN KEY (account, channel, operation)
                        REFERENCES cloud_deliveries(account, channel, operation)
                );
                CREATE TABLE IF NOT EXISTS cloud_delivery_decisions (
                    account TEXT NOT NULL, channel TEXT NOT NULL, operation TEXT NOT NULL,
                    decision_hash TEXT NOT NULL, part INTEGER NOT NULL,
                    PRIMARY KEY (account, channel, operation, decision_hash),
                    FOREIGN KEY (account, channel, operation)
                        REFERENCES cloud_deliveries(account, channel, operation)
                );
            """)
        os.chmod(path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def _digest(sender: str, chat: str, chunks: Sequence[str]) -> str:
        payload = json.dumps([sender, chat, list(chunks)], ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    def _reserve(self, account: str, channel: str, sender: str, chat: str, operation: str, chunks: Sequence[str]) -> None:
        digest = self._digest(sender, chat, chunks)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT digest FROM cloud_deliveries WHERE account=? AND channel=? AND operation=?",
                (account, channel, operation),
            ).fetchone()
            if row is not None:
                if row["digest"] != digest:
                    raise DeliveryConflict("operation already bound to a different destination or content")
                return
            usage = db.execute(
                "SELECT COUNT(*),COALESCE(SUM(parts),0) FROM cloud_deliveries WHERE account=?", (account,)
            ).fetchone()
            if int(usage[0]) >= MAX_ACCOUNT_DELIVERIES or int(usage[1]) + len(chunks) > MAX_ACCOUNT_PARTS:
                raise DeliveryFull("delivery history is full")
            db.execute(
                "INSERT INTO cloud_deliveries(account, channel, operation, digest, parts) VALUES (?, ?, ?, ?, ?)",
                (account, channel, operation, digest, len(chunks)),
            )
            db.executemany(
                "INSERT INTO cloud_delivery_parts(account, channel, operation, part, state) VALUES (?, ?, ?, ?, 'pending')",
                [(account, channel, operation, i) for i in range(len(chunks))],
            )

    def prepare(
        self, account: str, channel: str, sender: str, chat: str, operation: str, chunks: Sequence[str],
    ) -> None:
        """Persist the immutable destination and parts without sending anything."""
        if not all(isinstance(value, str) and value for value in (account, channel, sender, chat, operation)):
            raise ValueError("account, channel, sender, chat and operation are required")
        if channel not in {"telegram", "max"}:
            raise ValueError("unsupported messenger")
        if not chunks or any(not isinstance(chunk, str) or not chunk for chunk in chunks):
            raise ValueError("non-empty text chunks are required")
        self._reserve(account, channel, sender, chat, operation, tuple(chunks))

    def _next(self, account: str, channel: str, operation: str, claim: str) -> tuple[str, int, int]:
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT part, state FROM cloud_delivery_parts WHERE account=? AND channel=? AND operation=? ORDER BY part",
                (account, channel, operation),
            ).fetchall()
            sent = 0
            for row in rows:
                state = str(row["state"])
                if state == "sent":
                    sent += 1
                    continue
                if state != "pending":
                    # A 'sending' part might still be in flight, or its sender may have
                    # died after the API accepted it. Neither permits another request.
                    # Do not label a concurrent in-flight sender failed: its confirmation
                    # may still arrive. A stale sending state requires reconciliation.
                    return ("in_progress" if state == "sending" else "unknown"), int(row["part"]), sent
                db.execute(
                    "UPDATE cloud_delivery_parts SET state='sending', claim=? "
                    "WHERE account=? AND channel=? AND operation=? AND part=? AND state='pending'",
                    (claim, account, channel, operation, row["part"]),
                )
                return "send", int(row["part"]), sent
            return "sent", len(rows), sent

    def _finish(
        self, account: str, channel: str, operation: str, part: int, claim: str, state: str, remote_id: str = ""
    ) -> None:
        with closing(self._connect()) as db, db:
            changed = db.execute(
                "UPDATE cloud_delivery_parts SET state=?, remote_id=? WHERE account=? AND channel=? AND operation=? "
                "AND part=? AND state='sending' AND claim=?",
                (state, remote_id, account, channel, operation, part, claim),
            ).rowcount
            if changed != 1:
                raise RuntimeError("delivery claim lost")

    def part_state(self, account: str, channel: str, operation: str, part: int) -> str:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT state FROM cloud_delivery_parts WHERE account=? AND channel=? AND operation=? AND part=?",
                (account, channel, operation, part),
            ).fetchone()
        return str(row[0]) if row is not None else ""

    def confirm_sent(
        self, account: str, channel: str, sender: str, chat: str, operation: str,
        chunks: Sequence[str], part: int, remote_id: str,
    ) -> None:
        """Accept a part already confirmed by the trusted account-scoped relay."""
        if not isinstance(remote_id, str) or not remote_id:
            raise ValueError("confirmed platform message id is required")
        key = (account, channel, operation)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            header = db.execute(
                "SELECT digest FROM cloud_deliveries WHERE account=? AND channel=? AND operation=?", key
            ).fetchone()
            if header is None or header["digest"] != self._digest(sender, chat, chunks):
                raise DeliveryConflict("delivery destination or contents differ")
            row = db.execute(
                "SELECT state,remote_id FROM cloud_delivery_parts "
                "WHERE account=? AND channel=? AND operation=? AND part=?", (*key, part)
            ).fetchone()
            if row is None:
                raise DeliveryConflict("delivery part unavailable")
            if row["state"] == "sent":
                if row["remote_id"] != remote_id:
                    raise DeliveryConflict("platform message id conflicts")
                return
            if row["state"] not in {"sending", "unknown", "pending"}:
                raise DeliveryConflict("invalid delivery state")
            prior = db.execute(
                "SELECT 1 FROM cloud_delivery_parts WHERE account=? AND channel=? AND operation=? "
                "AND part<? AND state!='sent' LIMIT 1", (*key, part)
            ).fetchone()
            if prior:
                raise DeliveryConflict("previous part is not confirmed")
            db.execute(
                "UPDATE cloud_delivery_parts SET state='sent',claim='',remote_id=? "
                "WHERE account=? AND channel=? AND operation=? AND part=?", (remote_id, *key, part)
            )

    def retry_unknown(self, account: str, channel: str, operation: str, part: int, decision: str) -> None:
        """Reset one ambiguous part after a scoped, explicit user confirmation.

        The caller must verify the original sender and chat/topic before calling.
        The same confirmation can be replayed without resetting any later attempt.
        """
        if not isinstance(decision, str) or not 24 <= len(decision) <= 128:
            raise ValueError("a unique confirmation id is required")
        if isinstance(part, bool) or not isinstance(part, int) or part < 0:
            raise ValueError("invalid message part")
        key = (account, channel, operation)
        hashed = hashlib.sha256(decision.encode()).hexdigest()
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT part FROM cloud_delivery_decisions WHERE account=? AND channel=? AND operation=? AND decision_hash=?",
                (*key, hashed),
            ).fetchone()
            if old is not None:
                if old["part"] != part:
                    raise DeliveryConflict("confirmation belongs to another part")
                return
            count = db.execute(
                "SELECT COUNT(*) FROM cloud_delivery_decisions WHERE account=? AND channel=? AND operation=?", key
            ).fetchone()[0]
            if int(count) >= MAX_DECISIONS_PER_DELIVERY:
                raise DeliveryFull("too many confirmations for this delivery")
            row = db.execute(
                "SELECT state FROM cloud_delivery_parts WHERE account=? AND channel=? AND operation=? AND part=?", (*key, part)
            ).fetchone()
            # This local copy can still be pending when a different sender owns the
            # ambiguous relay claim. The caller must first obtain relay authorization.
            if row is None or row["state"] not in {"pending", "sending", "unknown"}:
                raise DeliveryConflict("only an undelivered part may be retried")
            db.execute(
                "UPDATE cloud_delivery_parts SET state='pending',claim='',remote_id='' "
                "WHERE account=? AND channel=? AND operation=? AND part=?", (*key, part)
            )
            db.execute("INSERT INTO cloud_delivery_decisions VALUES (?,?,?,?,?)", (*key, hashed, part))

    async def deliver(
        self, account: str, channel: str, sender: str, chat: str, operation: str,
        chunks: Sequence[str], send_chunk: Callable[[int, str], Awaitable[str]],
    ) -> dict[str, int | str]:
        """Deliver ordered chunks; `send_chunk` must issue exactly one platform request.

        Re-submit identical arguments after a restart to continue only unsent pending
        parts. Previously sent parts are skipped; an ambiguous part stops the delivery.
        `send_chunk` must return the non-empty platform message id on confirmation.
        The caller must validate the account and destination and retain the source reply.
        """
        chunks = tuple(chunks)
        self.prepare(account, channel, sender, chat, operation, chunks)
        while True:
            claim = os.urandom(16).hex()
            state, part, sent = self._next(account, channel, operation, claim)
            if state != "send":
                return {"state": state, "sent": sent}
            try:
                remote_id = await send_chunk(part, chunks[part])
                if not isinstance(remote_id, str) or not remote_id:
                    raise ValueError("platform did not confirm a message id")
            except asyncio.CancelledError:
                self._finish(account, channel, operation, part, claim, "unknown")
                raise
            except DeliveryDeferred:
                # The callback proves the relay did not grant its claim and no
                # platform request began. Only this definite outcome can be retried.
                self._finish(account, channel, operation, part, claim, "pending")
                return {"state": "in_progress", "sent": sent}
            except Exception:
                self._finish(account, channel, operation, part, claim, "unknown")
                return {"state": "unknown", "sent": sent}
            self._finish(account, channel, operation, part, claim, "sent", remote_id)
