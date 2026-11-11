"""Durable bridge from a verified private messenger to an account conversation.

The caller is responsible for proving the messenger identity and supplying callbacks
that authenticate it with the relay; credentials are never written to this journal.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from collections.abc import Awaitable, Callable, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

from nanomuse.channels.account_picker import AccountChatPicker, ConversationUnavailable
from nanomuse.channels.cloud_delivery import CloudDelivery, DeliveryConflict, DeliveryFull
from nanomuse.messenger_attachments import AttachmentError, parse_files, prepare_files, wire_files

MAX_ACCOUNT_STORED = 50 * 1024 * 1024
MAX_TOTAL_STORED = 512 * 1024 * 1024
MAX_ACCOUNT_JOBS = 1000
MAX_TOTAL_JOBS = 10000
MAX_RECOVERIES = 10000
MAX_ACCOUNT_PENDING_RECOVERIES = 50


class StorageFull(AttachmentError):
    """The durable file journal has reached its account or global limit."""


class DeliveryJournalFull(Exception):
    """The relay refused a new delivery because the customer's journal is full."""


class JobConflict(ValueError):
    """The same platform message id cannot address another conversation or content."""


class CloudMessengerBridge:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.picker = AccountChatPicker(root / "cloud-selections.sqlite3")
        self.delivery = CloudDelivery(root / "cloud-delivery.sqlite3")
        self._advancing: set[str] = set()
        self.path = root / "cloud-messenger-jobs.sqlite3"
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError:
            pass
        with closing(self._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                operation TEXT PRIMARY KEY, account TEXT NOT NULL, channel TEXT NOT NULL,
                sender TEXT NOT NULL, chat TEXT NOT NULL, message TEXT NOT NULL,
                conversation TEXT NOT NULL, text TEXT NOT NULL, state TEXT NOT NULL,
                next_attempt REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0)""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            if "next_attempt" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN next_attempt REAL NOT NULL DEFAULT 0")
            if "attempts" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_due ON jobs(state,next_attempt,account)")
            db.execute("""CREATE TABLE IF NOT EXISTS account_schedule (
                account TEXT PRIMARY KEY, last_served REAL NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS job_files (
                operation TEXT NOT NULL, position INTEGER NOT NULL, name TEXT NOT NULL,
                kind TEXT NOT NULL, sha256 TEXT NOT NULL, data BLOB,
                PRIMARY KEY(operation, position))""")
            db.execute("""CREATE TABLE IF NOT EXISTS job_notices (
                operation TEXT NOT NULL, code TEXT NOT NULL, PRIMARY KEY(operation,code))""")
            db.execute("""CREATE TABLE IF NOT EXISTS delivery_recoveries (
                token_hash TEXT PRIMARY KEY, account TEXT NOT NULL, channel TEXT NOT NULL,
                sender TEXT NOT NULL, chat TEXT NOT NULL, operation TEXT NOT NULL,
                part INTEGER NOT NULL, mid TEXT NOT NULL, chunks_digest TEXT NOT NULL,
                decision TEXT NOT NULL, expires REAL NOT NULL,
                confirmed INTEGER NOT NULL DEFAULT 0)""")
        os.chmod(self.path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA secure_delete=ON")
        return db

    @staticmethod
    def operation(account: str, channel: str, sender: str, chat: str, message: str) -> str:
        if not all((account, channel, sender, chat, message)):
            raise ValueError("verified account, destination and platform message id are required")
        raw = json.dumps([account, channel, sender, chat, message], ensure_ascii=False, separators=(",", ":"))
        return "msg_" + hashlib.sha256(raw.encode()).hexdigest()

    def _create(self, values: dict[str, str], files: list[dict[str, Any]], *, initial_state: str = "queued") -> dict[str, str]:
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE operation=?", (values["operation"],)).fetchone()
            if row is not None:
                result = dict(row)
                if any(result[k] != v for k, v in values.items()):
                    raise JobConflict("platform message id was reused with different content or destination")
                self._check_files(db, values["operation"], files)
                return result
            account_jobs = db.execute("SELECT COUNT(*) FROM jobs WHERE account=?", (values["account"],)).fetchone()[0]
            total_jobs = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            if account_jobs >= MAX_ACCOUNT_JOBS or total_jobs >= MAX_TOTAL_JOBS:
                raise StorageFull("operation journal is full")
            incoming = sum(len(item["data"]) for item in files)
            if incoming:
                per_account = db.execute(
                    "SELECT COALESCE(SUM(LENGTH(f.data)), 0) FROM job_files f "
                    "JOIN jobs j ON j.operation=f.operation WHERE j.account=?", (values["account"],),
                ).fetchone()[0]
                total = db.execute("SELECT COALESCE(SUM(LENGTH(data)), 0) FROM job_files").fetchone()[0]
                if per_account + incoming > MAX_ACCOUNT_STORED or total + incoming > MAX_TOTAL_STORED:
                    raise StorageFull("attachment journal is full")
            db.execute(
                "INSERT INTO jobs(operation, account, channel, sender, chat, message, conversation, text, state) "
                "VALUES(:operation, :account, :channel, :sender, :chat, :message, :conversation, :text, :initial_state)",
                {**values, "initial_state": initial_state},
            )
            db.executemany(
                "INSERT INTO job_files(operation, position, name, kind, sha256, data) VALUES (?, ?, ?, ?, ?, ?)",
                [(values["operation"], i, item["name"], item["kind"], item["sha256"], item["data"])
                 for i, item in enumerate(files)],
            )
            return {**values, "state": initial_state}

    @staticmethod
    def _check_files(db: sqlite3.Connection, operation: str, files: list[dict[str, Any]]) -> None:
        rows = db.execute(
            "SELECT name, kind, sha256 FROM job_files WHERE operation=? ORDER BY position", (operation,)
        ).fetchall()
        actual = [(row["name"], row["kind"], row["sha256"]) for row in rows]
        expected = [(row["name"], row["kind"], row["sha256"]) for row in files]
        if actual != expected:
            raise JobConflict("platform message id was reused with different attachments")

    def attachments(self, operation: str, account: str) -> list[dict[str, str]]:
        with closing(self._connect()) as db:
            owner = db.execute("SELECT account FROM jobs WHERE operation=?", (operation,)).fetchone()
            if owner is None or owner["account"] != account:
                raise AttachmentError("operation unavailable")
            rows = db.execute("SELECT name, kind, sha256, data FROM job_files WHERE operation=? ORDER BY position", (operation,)).fetchall()
        if any(row["data"] is None for row in rows):
            raise AttachmentError("attachments already released")
        return wire_files([dict(row) for row in rows])

    def _release_files(self, operation: str) -> None:
        with closing(self._connect()) as db, db:
            db.execute("UPDATE job_files SET data=NULL WHERE operation=?", (operation,))

    def _transition(self, operation: str, old: str, new: str) -> None:
        with closing(self._connect()) as db, db:
            db.execute("UPDATE jobs SET state=? WHERE operation=? AND state=?", (new, operation, old))

    def pending(self, limit: int = 20) -> list[dict[str, str]]:
        """A bounded read for status and tests; scheduling uses claim_due."""
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM jobs WHERE state IN ('queued','accepted') ORDER BY rowid LIMIT ?",
                (max(0, min(int(limit), 20)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def claim_due(self, limit: int = 20) -> list[dict[str, str]]:
        """A bounded round-robin claim; a crash makes the rows due again after a minute."""
        count = max(0, min(int(limit), 20))
        if not count:
            return []
        now = time.time()
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "WITH ranked AS (SELECT rowid rid, ROW_NUMBER() OVER "
                "(PARTITION BY account ORDER BY next_attempt,rowid) n "
                "FROM jobs WHERE state IN ('queued','accepted') AND next_attempt<=?) "
                "SELECT jobs.* FROM ranked JOIN jobs ON jobs.rowid=ranked.rid "
                "LEFT JOIN account_schedule AS sched ON sched.account=jobs.account "
                "ORDER BY ranked.n,COALESCE(sched.last_served,0),jobs.next_attempt,ranked.rid LIMIT ?", (now, count),
            ).fetchall()
            db.executemany(
                "UPDATE jobs SET next_attempt=? WHERE operation=?", [(now + 60, row["operation"]) for row in rows],
            )
            db.executemany(
                "INSERT INTO account_schedule(account,last_served) VALUES(?,?) "
                "ON CONFLICT(account) DO UPDATE SET last_served=excluded.last_served",
                [(row["account"], now) for row in rows],
            )
        return [dict(row) for row in rows]

    def defer(self, operation: str, *, failed: bool) -> None:
        """Back off outages without starving other accounts; normal polling waits five seconds."""
        with closing(self._connect()) as db, db:
            row = db.execute("SELECT attempts FROM jobs WHERE operation=? AND state IN ('queued','accepted')", (operation,)).fetchone()
            if row is None:
                return
            attempts = min(int(row[0]) + 1, 7) if failed else 0
            delay = min(300, 5 * (2 ** attempts)) if failed else 5
            db.execute("UPDATE jobs SET attempts=?,next_attempt=? WHERE operation=?", (attempts, time.time() + delay, operation))

    def claim_notice(self, operation: str, code: str) -> bool:
        """At most one explanatory status per condition and operation across restarts."""
        with closing(self._connect()) as db, db:
            return bool(db.execute(
                "INSERT OR IGNORE INTO job_notices(operation,code) VALUES(?,?)", (operation, code),
            ).rowcount)

    def recovery_jobs(self, account: str, channel: str, sender: str, chat: str) -> list[dict[str, str]]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM jobs WHERE account=? AND channel=? AND sender=? AND chat=? "
                "AND state IN ('accepted','unknown','held_voice','held_files','delivery_full') ORDER BY rowid DESC LIMIT 20",
                (account, channel, sender, chat),
            ).fetchall()
        return [dict(row) for row in rows]

    def issue_recovery(self, job: dict[str, str], part: int, mid: str, chunks: Sequence[str]) -> str:
        token = secrets.token_urlsafe(18)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        digest = hashlib.sha256(json.dumps(list(chunks), ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        decision = secrets.token_urlsafe(32)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM delivery_recoveries WHERE confirmed=0 AND expires<?", (time.time(),))
            count = db.execute("SELECT COUNT(*) FROM delivery_recoveries").fetchone()[0]
            account_pending = db.execute(
                "SELECT COUNT(*) FROM delivery_recoveries WHERE account=? AND confirmed=0", (job["account"],),
            ).fetchone()[0]
            if count >= MAX_RECOVERIES or account_pending >= MAX_ACCOUNT_PENDING_RECOVERIES:
                raise StorageFull("recovery journal is full")
            db.execute(
                "INSERT INTO delivery_recoveries(token_hash,account,channel,sender,chat,operation,part,mid,chunks_digest,decision,expires) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (token_hash, job["account"], job["channel"], job["sender"], job["chat"],
                 job["operation"], part, mid, digest, decision, time.time() + 900),
            )
        return token

    def recovery(
        self, token: str, account: str, channel: str, sender: str, chat: str,
    ) -> dict[str, Any] | None:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT r.*, j.conversation, j.text, j.message, j.state FROM delivery_recoveries r "
                "JOIN jobs j ON j.operation=r.operation WHERE r.token_hash=? AND r.account=? "
                "AND r.channel=? AND r.sender=? AND r.chat=? AND (r.expires>? OR r.confirmed=1)",
                (token_hash, account, channel, sender, chat, time.time()),
            ).fetchone()
        return dict(row) if row is not None else None

    def mark_recovery_confirmed(self, token: str, account: str, channel: str, sender: str, chat: str) -> bool:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with closing(self._connect()) as db, db:
            return bool(db.execute(
                "UPDATE delivery_recoveries SET confirmed=1 WHERE token_hash=? AND account=? "
                "AND channel=? AND sender=? AND chat=? AND (expires>? OR confirmed=1)",
                (token_hash, account, channel, sender, chat, time.time()),
            ).rowcount)

    def hold_attachments(
        self, account: str, channel: str, sender: str, chat: str, message: str,
        text: str, files: list[Any],
    ) -> dict[str, str]:
        """Keep the original attachments without invoking ASR or a model."""
        self.picker._identity(account, channel, sender, chat)
        prepared = prepare_files(files)
        if not prepared:
            raise AttachmentError("attachment required")
        state = "held_voice" if any(item["kind"] == "audio" for item in prepared) else "held_files"
        return self._create({
            "operation": self.operation(account, channel, sender, chat, message),
            "account": account, "channel": channel, "sender": sender, "chat": chat,
            "message": message, "conversation": "", "text": text,
        }, prepared, initial_state=state)

    def requeue_recovered(self, operation: str) -> None:
        with closing(self._connect()) as db, db:
            db.execute("UPDATE jobs SET state='accepted',next_attempt=0,attempts=0 WHERE operation=? AND state='unknown'", (operation,))

    def record(
        self, account: str, channel: str, sender: str, chat: str, message: str,
        text: str, catalog: dict[str, Any], files: list[Any] | None = None,
    ) -> dict[str, str]:
        prepared = prepare_files(files or [])
        if not text.strip() and not prepared:
            raise ValueError("message text or attachments are required")
        operation = self.operation(account, channel, sender, chat, message)
        with closing(self._connect()) as db:
            existing = db.execute("SELECT * FROM jobs WHERE operation=?", (operation,)).fetchone()
        if existing is not None:
            job = dict(existing)
            if any(job[key] != value for key, value in {
                "account": account, "channel": channel, "sender": sender, "chat": chat,
                "message": message, "text": text,
            }.items()):
                raise JobConflict("platform message id was reused with different content or destination")
            with closing(self._connect()) as db:
                self._check_files(db, operation, prepared)
            if job["conversation"] not in self.picker._active(account, catalog):
                raise ConversationUnavailable("conversation unavailable")
            return job
        cid = self.picker.selected(account, channel, sender, chat, catalog)
        if not cid:
            raise ConversationUnavailable("select a conversation first")
        return self._create({
            "operation": operation,
            "account": account, "channel": channel, "sender": sender, "chat": chat,
            "message": message, "conversation": cid, "text": text,
        }, prepared)

    async def advance(
        self, job: dict[str, str], catalog: dict[str, Any],
        send_request: Callable[[dict[str, str]], Awaitable[dict[str, Any]]],
        get_result: Callable[[dict[str, str]], Awaitable[dict[str, Any]]],
        split: Callable[[str], Sequence[str]],
        send_part: Callable[[dict[str, str], int, str], Awaitable[Any]],
        prepare: Callable[[dict[str, str], Sequence[str]], Awaitable[Any]] | None = None,
    ) -> str:
        operation = job["operation"]
        if operation in self._advancing:
            return "accepted"
        self._advancing.add(operation)
        try:
            return await self._advance_one(job, catalog, send_request, get_result, split, send_part, prepare)
        finally:
            self._advancing.discard(operation)

    async def _advance_one(
        self, job: dict[str, str], catalog: dict[str, Any],
        send_request: Callable[[dict[str, str]], Awaitable[dict[str, Any]]],
        get_result: Callable[[dict[str, str]], Awaitable[dict[str, Any]]],
        split: Callable[[str], Sequence[str]],
        send_part: Callable[[dict[str, str], int, str], Awaitable[Any]],
        prepare: Callable[[dict[str, str], Sequence[str]], Awaitable[Any]] | None = None,
    ) -> str:
        # Refresh with the customer's live catalogue, not the current global selection:
        # the person may have switched to another chat while this job was running.
        if job["conversation"] not in self.picker._active(job["account"], catalog):
            raise ConversationUnavailable("conversation unavailable")
        if job["state"] == "queued":
            result = await send_request(job)
            state = result.get("state")
            if state == "unknown":
                self._transition(job["operation"], "queued", "unknown")
                return "unknown"
            if state == "rejected":
                self._transition(job["operation"], "queued", "rejected")
                self._release_files(job["operation"])
                return "rejected"
            if state != "accepted":
                return "queued"
            self._transition(job["operation"], "queued", "accepted")
            # Keep the original bytes: conversation sync currently carries file
            # metadata but not a verified copy usable by the customer's other devices.
            job = {**job, "state": "accepted"}
        if job["state"] != "accepted":
            return job["state"]
        result = await get_result(job)
        if result.get("state") == "unknown":
            self._transition(job["operation"], "accepted", "unknown")
            return "unknown"
        if result.get("state") not in {"completed", "failed"}:
            return "accepted"
        text = str(result.get("text") or "")
        try:
            files = wire_files(parse_files(result.get("files", [])))
        except AttachmentError:
            self._transition(job["operation"], "accepted", "unknown")
            return "unknown"
        if not text and not files:
            self._transition(job["operation"], "accepted", "unknown")
            return "unknown"
        chunks = tuple(split(text)) + tuple(json.dumps({"type": "file", **item}, ensure_ascii=False) for item in files)
        response_mid = str(result.get("mid") or "")
        response_job = {**job, "response_mid": response_mid}
        if prepare is not None:
            if not response_mid:
                self._transition(job["operation"], "accepted", "unknown")
                return "unknown"
            try:
                remote = await prepare(response_job, chunks)
                if isinstance(remote, dict) and isinstance(remote.get("parts"), list):
                    self.delivery.prepare(job["account"], job["channel"], job["sender"], job["chat"], job["operation"], chunks)
                    for part in remote["parts"]:
                        if not isinstance(part, dict) or type(part.get("part")) is not int:
                            raise DeliveryConflict("invalid relay delivery state")
                        index, state = part["part"], part.get("state")
                        if state == "sent" and part.get("remote_id"):
                            self.delivery.confirm_sent(
                                job["account"], job["channel"], job["sender"], job["chat"], job["operation"],
                                chunks, index, str(part["remote_id"]),
                            )
                        elif state == "unknown" or (
                            state == "sending" and self.delivery.part_state(
                                job["account"], job["channel"], job["operation"], index,
                            ) == "sending"
                        ):
                            self._transition(job["operation"], "accepted", "unknown")
                            return "unknown"
            except (DeliveryJournalFull, DeliveryFull):
                self._transition(job["operation"], "accepted", "delivery_full")
                return "delivery_full"
            except DeliveryConflict:
                self._transition(job["operation"], "accepted", "unknown")
                return "unknown"

        async def send_one(index: int, chunk: str) -> Any:
            return await send_part(response_job, index, chunk)

        try:
            delivered = await self.delivery.deliver(
                job["account"], job["channel"], job["sender"], job["chat"], job["operation"], chunks, send_one,
            )
        except DeliveryFull:
            self._transition(job["operation"], "accepted", "delivery_full")
            return "delivery_full"
        except DeliveryConflict:
            self._transition(job["operation"], "accepted", "unknown")
            return "unknown"
        state = str(delivered["state"])
        if state == "in_progress":
            return "accepted"
        self._transition(job["operation"], "accepted", "done" if state == "sent" else "unknown")
        return "done" if state == "sent" else "unknown"
