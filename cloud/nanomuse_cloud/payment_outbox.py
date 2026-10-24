"""Transactional payment notices, prepared for a future live settlement release.

Enqueue with the same connection/transaction as the real ledger commit. Dispatch is
explicit, outside transactions, and requires a sink that deduplicates event_id.
The existing SMTP/webhook senders do not guarantee this, so they are not wired here.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import time
import uuid
from collections.abc import Awaitable, Callable

SCHEMA = """CREATE TABLE IF NOT EXISTS payment_notice_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    order_id TEXT NOT NULL REFERENCES tbank_orders(id),
    account_id TEXT NOT NULL REFERENCES accounts(id),
    kind TEXT NOT NULL,
    amount_kopecks INTEGER NOT NULL,
    cumulative_refund_kopecks INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at INTEGER NOT NULL DEFAULT 0,
    lease_until INTEGER NOT NULL DEFAULT 0,
    lease_token TEXT NOT NULL DEFAULT '',
    sent_at INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT ''
)"""


class PaymentOutbox:
    def __init__(self, db):
        self.db = db
        with db.tx() as c:
            c.execute(SCHEMA)
            c.execute("CREATE INDEX IF NOT EXISTS payment_notice_due ON payment_notice_outbox(state,next_attempt_at,lease_until)")

    @staticmethod
    def enqueue_settled(c, order_id: str, *, kind: str, now: int | None = None) -> str:
        """Hook AFTER live ledger settlement, in its transaction, never from a redirect.

        Captures have one identity per order. Refunds use the reconciled cumulative
        refund amount: repeats collapse, distinct partial refunds remain distinct.
        This hook deliberately does not issue or claim to deliver a fiscal receipt.
        """
        if not c.in_transaction:
            raise ValueError("payment_notice_transaction_required")
        if kind not in {"payment_confirmed", "payment_refunded"}:
            raise ValueError("payment_notice_kind")
        row = c.execute("SELECT * FROM tbank_orders WHERE id=?", (order_id,)).fetchone()
        if row is None:
            raise ValueError("payment_notice_order")
        if not row["terminal"] or row["terminal"].endswith("DEMO"):
            raise ValueError("payment_notice_demo_forbidden")
        if row["credited"] != 1 or not row["payment_id"]:
            raise ValueError("payment_notice_unsettled")
        refund = row["refunded"]
        if not 0 <= refund <= row["amount"]:
            raise ValueError("payment_notice_amount")
        if kind == "payment_refunded" and (refund == 0 or row["status"] not in {"REFUNDED", "PARTIAL_REFUNDED"}):
            raise ValueError("payment_notice_unsettled")
        if kind == "payment_confirmed" and row["status"] != "CONFIRMED":
            raise ValueError("payment_notice_unsettled")
        marker = refund if kind == "payment_refunded" else 0
        identity = json.dumps([row["terminal"], row["payment_id"], row["id"], kind, marker], separators=(",", ":"))
        event_id = hashlib.sha256(identity.encode()).hexdigest()
        c.execute(
            "INSERT OR IGNORE INTO payment_notice_outbox"
            "(event_id,order_id,account_id,kind,amount_kopecks,cumulative_refund_kopecks,created_at) VALUES(?,?,?,?,?,?,?)",
            (event_id, order_id, row["account_id"], kind, row["amount"], marker, int(time.time()) if now is None else now),
        )
        return event_id

    def claim(self, *, now: int, lease_seconds: int = 120) -> dict | None:
        if lease_seconds < 1:
            raise ValueError("payment_notice_lease")
        token = uuid.uuid4().hex
        with self.db.tx() as c:
            row = c.execute(
                "UPDATE payment_notice_outbox SET state='sending',attempts=attempts+1,lease_token=?,lease_until=? "
                "WHERE event_id=(SELECT event_id FROM payment_notice_outbox "
                "WHERE (state='pending' AND next_attempt_at<=?) OR (state='sending' AND lease_until<=?) "
                "ORDER BY id LIMIT 1) RETURNING *",
                (token, now + lease_seconds, now, now),
            ).fetchone()
            return dict(row) if row else None

    def acknowledge(self, row: dict, *, now: int) -> bool:
        with self.db.tx() as c:
            return (
                c.execute(
                    "UPDATE payment_notice_outbox SET state='sent',sent_at=?,lease_until=0,lease_token='',last_error='' "
                    "WHERE event_id=? AND state='sending' AND lease_token=?",
                    (now, row["event_id"], row["lease_token"]),
                ).rowcount
                == 1
            )

    def retry(self, row: dict, *, now: int) -> bool:
        # Exception text can contain addresses, credentials, provider response bodies.
        # Keep a stable code only. Uncertain sends retry with the SAME idempotency key.
        delay = min(3600, 30 * 2 ** min(max(0, row["attempts"] - 1), 7))
        with self.db.tx() as c:
            return (
                c.execute(
                    "UPDATE payment_notice_outbox SET state='pending',next_attempt_at=?,lease_until=0,lease_token='',last_error='delivery_failed' "
                    "WHERE event_id=? AND state='sending' AND lease_token=?",
                    (now + delay, row["event_id"], row["lease_token"]),
                ).rowcount
                == 1
            )

    async def dispatch_one(self, sink: Callable[[dict, str], Awaitable[None]], *, now: int | None = None) -> bool:
        """Sink MUST durably deduplicate event_id; SMTP by itself is insufficient.

        No claim of exactly-once email delivery: a crash after external acceptance but
        before acknowledge causes a retry. The stable event_id makes that retry safe
        only with an idempotent receiver. Recipient lookup/rendering belongs to sink.
        """
        current = int(time.time()) if now is None else now
        row = self.claim(now=current)
        if row is None:
            return False
        event = {key: row[key] for key in ("event_id", "order_id", "account_id", "kind", "amount_kopecks", "cumulative_refund_kopecks")}
        try:
            result = sink(event, row["event_id"])
            if not inspect.isawaitable(result):
                raise TypeError("payment_notice_async_sink_required")
            await result
        except Exception:
            self.retry(row, now=int(time.time()) if now is None else now)
            return False
        return self.acknowledge(row, now=int(time.time()) if now is None else now)
