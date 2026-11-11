"""Account-scoped, durable arbitration for messenger message parts.

Claiming a part precedes any external request. An unfinished claim is never
expired or automatically taken over: a platform may have accepted that request.
Only callers using the same canonical message, destination and splitting can
coordinate. Legacy senders remain outside this protocol until migrated.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from fastapi import Depends, Request

from .service import CloudError

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN = re.compile(r"[a-zA-Z0-9_-]{24,128}\Z")

# Until durable tombstone compaction exists, these lifetime caps prevent one
# account from consuming unbounded relay storage without reopening old deliveries.
MAX_ACCOUNT_DELIVERIES = 10_000
MAX_ACCOUNT_PARTS = 50_000
# A second cap bounds growth across accounts, including newly created ones.
MAX_TOTAL_DELIVERIES = 100_000
MAX_TOTAL_PARTS = 500_000
MAX_DECISIONS_PER_DELIVERY = 32


class DeliveryJournal:
    def __init__(self, db: Any):
        self.db = db
        # A new table is added idempotently to existing installations. We keep only
        # destination/content hashes and platform message ids, never message text.
        with self.db.tx_durable() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS delivery_journal (
                account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                mid TEXT NOT NULL, channel TEXT NOT NULL, target_hash TEXT NOT NULL,
                digest TEXT NOT NULL, parts INTEGER NOT NULL,
                PRIMARY KEY (account_id, mid, channel, target_hash)
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS delivery_journal_parts (
                account_id TEXT NOT NULL, mid TEXT NOT NULL, channel TEXT NOT NULL, target_hash TEXT NOT NULL,
                part INTEGER NOT NULL, state TEXT NOT NULL, claim_hash TEXT NOT NULL DEFAULT '',
                remote_id TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (account_id, mid, channel, target_hash, part),
                FOREIGN KEY (account_id, mid, channel, target_hash)
                    REFERENCES delivery_journal(account_id, mid, channel, target_hash) ON DELETE CASCADE
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS delivery_journal_decisions (
                account_id TEXT NOT NULL, mid TEXT NOT NULL, channel TEXT NOT NULL, target_hash TEXT NOT NULL,
                decision_hash TEXT NOT NULL, part INTEGER NOT NULL,
                PRIMARY KEY (account_id, mid, channel, target_hash, decision_hash),
                FOREIGN KEY (account_id, mid, channel, target_hash)
                    REFERENCES delivery_journal(account_id, mid, channel, target_hash) ON DELETE CASCADE
            )""")
            # Backfill once for existing journals while holding the write lock; all
            # later inserts/deletes update these counters in the same transaction.
            c.execute("""CREATE TABLE IF NOT EXISTS delivery_journal_usage (
                account_id TEXT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
                deliveries INTEGER NOT NULL, parts INTEGER NOT NULL
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS delivery_journal_totals (
                singleton INTEGER PRIMARY KEY CHECK (singleton=1),
                deliveries INTEGER NOT NULL, parts INTEGER NOT NULL
            )""")
            c.execute("""INSERT OR IGNORE INTO delivery_journal_totals
                SELECT 1,COUNT(*),COALESCE(SUM(parts),0) FROM delivery_journal""")
            c.execute("""INSERT OR IGNORE INTO delivery_journal_usage
                SELECT account_id,COUNT(*),COALESCE(SUM(parts),0)
                FROM delivery_journal GROUP BY account_id""")
            c.execute("""CREATE TRIGGER IF NOT EXISTS delivery_journal_count_insert
                AFTER INSERT ON delivery_journal BEGIN
                    INSERT INTO delivery_journal_usage(account_id,deliveries,parts)
                    VALUES(NEW.account_id,1,NEW.parts)
                    ON CONFLICT(account_id) DO UPDATE SET
                        deliveries=deliveries+1,parts=parts+NEW.parts;
                    UPDATE delivery_journal_totals SET deliveries=deliveries+1,parts=parts+NEW.parts
                    WHERE singleton=1;
                END""")
            c.execute("""CREATE TRIGGER IF NOT EXISTS delivery_journal_count_delete
                AFTER DELETE ON delivery_journal BEGIN
                    UPDATE delivery_journal_usage SET deliveries=deliveries-1,parts=parts-OLD.parts
                    WHERE account_id=OLD.account_id;
                    UPDATE delivery_journal_totals SET deliveries=deliveries-1,parts=parts-OLD.parts
                    WHERE singleton=1;
                END""")

    @staticmethod
    def _identity(account: str, body: dict[str, Any]) -> tuple[str, str, str, str]:
        try:
            mid = str(uuid.UUID(str(body.get("mid") or "")))
        except (ValueError, AttributeError):
            raise CloudError(400, "bad_mid", "A canonical message id is required") from None
        channel = body.get("channel")
        chat = body.get("chat")
        if not isinstance(channel, str) or channel not in {"telegram", "max"} or not isinstance(chat, str) or not chat or len(chat) > 120:
            raise CloudError(400, "bad_destination", "A messenger and canonical chat are required")
        return account, mid, channel, hashlib.sha256(chat.encode()).hexdigest()

    @staticmethod
    def _token(body: dict[str, Any]) -> str:
        token = body.get("claim")
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise CloudError(400, "bad_claim", "A unique claim is required")
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def _part(body: dict[str, Any]) -> int:
        part = body.get("part")
        if isinstance(part, bool) or not isinstance(part, int) or part < 0 or part >= 100:
            raise CloudError(400, "bad_part", "Invalid message part")
        return part

    def prepare(self, account: str, body: dict[str, Any]) -> dict[str, Any]:
        key = self._identity(account, body)
        chunks = body.get("chunks")
        if not isinstance(chunks, list) or not 1 <= len(chunks) <= 100 or any(
            not isinstance(chunk, str) or not _HEX.fullmatch(chunk) for chunk in chunks
        ):
            raise CloudError(400, "bad_parts", "A list of part digests is required")
        digest = hashlib.sha256(json.dumps(chunks, separators=(",", ":")).encode()).hexdigest()
        with self.db.tx_durable() as c:
            # Legacy claims are ambiguous even after their timeout: the old client
            # might already have sent a message. Never start a new protocol delivery.
            legacy = c.execute(
                "SELECT 1 FROM sync_deliveries WHERE account_id=? AND mid=? AND channel=? AND chat=?",
                (account, key[1], key[2], body["chat"]),
            ).fetchone()
            if legacy is not None:
                raise CloudError(409, "legacy_delivery_unknown", "A legacy sender may already have delivered this message")
            row = c.execute(
                "SELECT digest FROM delivery_journal WHERE account_id=? AND mid=? AND channel=? AND target_hash=?", key
            ).fetchone()
            if row is not None:
                if row["digest"] != digest:
                    raise CloudError(409, "delivery_conflict", "Message parts do not match the existing delivery")
            else:
                usage = c.execute(
                    "SELECT deliveries,parts FROM delivery_journal_usage WHERE account_id=?", (account,)
                ).fetchone()
                total = c.execute(
                    "SELECT deliveries,parts FROM delivery_journal_totals WHERE singleton=1"
                ).fetchone()
                deliveries, parts = (int(usage[0]), int(usage[1])) if usage else (0, 0)
                if (
                    deliveries >= MAX_ACCOUNT_DELIVERIES or parts + len(chunks) > MAX_ACCOUNT_PARTS
                    or int(total[0]) >= MAX_TOTAL_DELIVERIES or int(total[1]) + len(chunks) > MAX_TOTAL_PARTS
                ):
                    raise CloudError(
                        429, "delivery_journal_full",
                        "Delivery history is full; contact support before sending more messenger messages",
                    )
                c.execute("INSERT INTO delivery_journal VALUES (?,?,?,?,?,?)", (*key, digest, len(chunks)))
                c.executemany(
                    "INSERT INTO delivery_journal_parts(account_id,mid,channel,target_hash,part,state) VALUES (?,?,?,?,?,'pending')",
                    [(*key, index) for index in range(len(chunks))],
                )
            rows = c.execute(
                "SELECT part,state,remote_id FROM delivery_journal_parts "
                "WHERE account_id=? AND mid=? AND channel=? AND target_hash=? ORDER BY part", key
            ).fetchall()
            return {"parts": [dict(row) for row in rows]}

    def claim(self, account: str, body: dict[str, Any]) -> dict[str, Any]:
        key, part, claim = self._identity(account, body), self._part(body), self._token(body)
        with self.db.tx_durable() as c:
            row = c.execute(
                "SELECT state,remote_id FROM delivery_journal_parts "
                "WHERE account_id=? AND mid=? AND channel=? AND target_hash=? AND part=?", (*key, part)
            ).fetchone()
            if row is None:
                raise CloudError(404, "delivery_not_found", "Prepare this delivery first")
            if row["state"] != "pending":
                return {"state": str(row["state"]), "remote_id": str(row["remote_id"])}
            prior = c.execute(
                "SELECT 1 FROM delivery_journal_parts WHERE account_id=? AND mid=? AND channel=? AND target_hash=? "
                "AND part<? AND state!='sent' LIMIT 1", (*key, part)
            ).fetchone()
            if prior:
                return {"state": "blocked"}
            changed = c.execute(
                "UPDATE delivery_journal_parts SET state='sending',claim_hash=? "
                "WHERE account_id=? AND mid=? AND channel=? AND target_hash=? AND part=? AND state='pending'",
                (claim, *key, part),
            ).rowcount
            return {"state": "claimed" if changed == 1 else "sending"}

    def finish(self, account: str, body: dict[str, Any]) -> dict[str, Any]:
        key, part, claim = self._identity(account, body), self._part(body), self._token(body)
        state, remote = body.get("state"), body.get("remote_id", "")
        if not isinstance(state, str) or state not in {"sent", "unknown"} or not isinstance(remote, str) or len(remote) > 256:
            raise CloudError(400, "bad_result", "A valid delivery outcome is required")
        if (state == "sent" and not remote) or (state == "unknown" and remote):
            raise CloudError(400, "bad_result", "A sent part requires a confirmed platform message id")
        with self.db.tx_durable() as c:
            row = c.execute(
                "SELECT state,claim_hash,remote_id FROM delivery_journal_parts "
                "WHERE account_id=? AND mid=? AND channel=? AND target_hash=? AND part=?", (*key, part)
            ).fetchone()
            if row is None:
                raise CloudError(404, "delivery_not_found", "Prepare this delivery first")
            if row["claim_hash"] != claim:
                raise CloudError(409, "delivery_not_owner", "Only the original sender may finish this part")
            if row["state"] == state and row["remote_id"] == remote:
                return {"state": state}
            if row["state"] != "sending":
                raise CloudError(409, "delivery_conflict", "The part already has an outcome")
            c.execute(
                "UPDATE delivery_journal_parts SET state=?,remote_id=? "
                "WHERE account_id=? AND mid=? AND channel=? AND target_hash=? AND part=?",
                (state, remote, *key, part),
            )
            return {"state": state}

    def retry(self, account: str, body: dict[str, Any]) -> dict[str, Any]:
        """Authorize one new attempt only after explicit scoped user confirmation.

        The caller must authenticate the person and their original topic before
        invoking this endpoint. Old in-flight attempts may still succeed; that is why
        the confirmation must warn the person about a possible duplicate.
        """
        key, part = self._identity(account, body), self._part(body)
        decision = body.get("decision")
        if not isinstance(decision, str) or not _TOKEN.fullmatch(decision):
            raise CloudError(400, "bad_decision", "A unique confirmation id is required")
        decision_hash = hashlib.sha256(decision.encode()).hexdigest()
        with self.db.tx_durable() as c:
            old = c.execute(
                "SELECT part FROM delivery_journal_decisions "
                "WHERE account_id=? AND mid=? AND channel=? AND target_hash=? AND decision_hash=?",
                (*key, decision_hash),
            ).fetchone()
            if old is not None:
                if old["part"] != part:
                    raise CloudError(409, "delivery_conflict", "Confirmation already used for another part")
                # Never reset a later in-flight or completed attempt on replay.
                return {"state": "authorized"}
            decisions = c.execute(
                "SELECT COUNT(*) FROM delivery_journal_decisions "
                "WHERE account_id=? AND mid=? AND channel=? AND target_hash=?", key,
            ).fetchone()[0]
            if int(decisions) >= MAX_DECISIONS_PER_DELIVERY:
                raise CloudError(429, "delivery_decisions_full", "Too many confirmations; contact support")
            row = c.execute(
                "SELECT state FROM delivery_journal_parts "
                "WHERE account_id=? AND mid=? AND channel=? AND target_hash=? AND part=?", (*key, part)
            ).fetchone()
            if row is None:
                raise CloudError(404, "delivery_not_found", "Delivery part was not found")
            if row["state"] not in {"sending", "unknown"}:
                raise CloudError(409, "delivery_not_unknown", "Only an ambiguous part may be retried")
            c.execute(
                "UPDATE delivery_journal_parts SET state='pending',claim_hash='',remote_id='' "
                "WHERE account_id=? AND mid=? AND channel=? AND target_hash=? AND part=?", (*key, part)
            )
            c.execute("INSERT INTO delivery_journal_decisions VALUES (?,?,?,?,?,?)", (*key, decision_hash, part))
            return {"state": "authorized"}


def install(app: Any, db: Any, caller_dep: Any) -> None:
    journal = DeliveryJournal(db)

    async def body_of(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except ValueError:
            raise CloudError(400, "bad_request", "Invalid request") from None
        if not isinstance(body, dict):
            raise CloudError(400, "bad_request", "Invalid request")
        return body

    @app.post("/v1/delivery/prepare")
    async def prepare(request: Request, caller: Any = Depends(caller_dep)) -> dict[str, Any]:
        return journal.prepare(caller.account_id, await body_of(request))

    @app.post("/v1/delivery/claim")
    async def claim(request: Request, caller: Any = Depends(caller_dep)) -> dict[str, Any]:
        return journal.claim(caller.account_id, await body_of(request))

    @app.post("/v1/delivery/retry")
    async def retry(request: Request, caller: Any = Depends(caller_dep)) -> dict[str, Any]:
        return journal.retry(caller.account_id, await body_of(request))

    @app.post("/v1/delivery/finish")
    async def finish(request: Request, caller: Any = Depends(caller_dep)) -> dict[str, Any]:
        return journal.finish(caller.account_id, await body_of(request))
