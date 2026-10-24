"""Atomic live payment accounting. Never accepts DEMO funds as real account credit.

All amounts are tied to an order's captured conversion rate and immutable plan snapshot.
External bank/receipt/mail requests belong outside the transaction.
"""

from __future__ import annotations

import json
import time
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from .payment_outbox import PaymentOutbox
from .service import CloudError

if TYPE_CHECKING:
    import sqlite3

    from .config import Settings
    from .service import Cloud

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS tbank_live_events (
    order_id TEXT NOT NULL REFERENCES tbank_orders(id),
    kind TEXT NOT NULL, cumulative_refund INTEGER NOT NULL,
    amount_delta INTEGER NOT NULL, grant_delta_uy INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY(order_id,kind,cumulative_refund))""",
    """CREATE TABLE IF NOT EXISTS tbank_live_state (
    order_id TEXT PRIMARY KEY REFERENCES tbank_orders(id),
    account_id TEXT NOT NULL REFERENCES accounts(id),
    total_uy INTEGER NOT NULL, refunded_uy INTEGER NOT NULL DEFAULT 0,
    captured_at INTEGER NOT NULL, plan_snapshot TEXT NOT NULL DEFAULT '{}',
    previous_subscription TEXT NOT NULL DEFAULT '{}',
    entitlement_before TEXT NOT NULL DEFAULT '{}',
    entitlement_after TEXT NOT NULL DEFAULT '{}',
    review_required INTEGER NOT NULL DEFAULT 0)""",
)


def credit_units(settings: Settings, amount: int) -> int:
    """Same inverse display conversion as Settings, rounded once, without float drift."""
    rub = Decimal(str(settings.rub_per_usd))
    yuan = Decimal(str(settings.usd_cny))
    fee = Decimal(str(settings.topup_fee_pct))
    if not all(v.is_finite() for v in (rub, yuan, fee)) or rub <= 0 or yuan <= 0 or fee < 0:
        raise CloudError(503, "payment_rate", "Не настроен курс рабочего баланса")
    return int((Decimal(amount) / 100 / rub * yuan / (1 + fee / 100) * 1_000_000).quantize(Decimal(1), rounding=ROUND_HALF_UP))


class LiveBilling:
    def __init__(self, cloud: Cloud):
        self.cloud, self.db = cloud, cloud.db
        self.outbox = PaymentOutbox(self.db)
        with self.db.tx() as c:
            for sql in SCHEMA:
                c.execute(sql)

    def validate_plan_purchase(self, c: sqlite3.Connection, account_id: str, plan_id: str, *, now: int) -> None:
        if not plan_id:
            return
        current = c.execute("SELECT * FROM subscriptions WHERE account_id=?", (account_id,)).fetchone()
        if current and current["expires_at"] > now and current["plan"] != plan_id:
            raise CloudError(409, "payment_plan_change", "Действует другой пакет. Дождитесь его окончания или напишите в поддержку")
        pending = c.execute(
            "SELECT 1 FROM tbank_orders WHERE account_id=? AND plan<>'' AND terminal NOT LIKE '%DEMO' "
            "AND status IN ('CREATING','NEW','FORM_SHOWED','AUTHORIZING','AUTHORIZED','CONFIRMING') LIMIT 1",
            (account_id,),
        ).fetchone()
        if pending:
            raise CloudError(409, "payment_plan_pending", "Уже есть незавершённый заказ пакета. Проверьте его статус")

    @staticmethod
    def _subscription(c: sqlite3.Connection, account: str) -> dict:
        row = c.execute("SELECT * FROM subscriptions WHERE account_id=?", (account,)).fetchone()
        return dict(row) if row else {}

    @staticmethod
    def _write_subscription(c: sqlite3.Connection, state: dict, account: str) -> None:
        if not state:
            c.execute("DELETE FROM subscriptions WHERE account_id=?", (account,))
            return
        keys = ("account_id", "plan", "name", "price_rub", "discount_pct", "connect", "started_at", "expires_at", "paid_uy")
        c.execute(
            "INSERT INTO subscriptions(" + ",".join(keys) + ") VALUES(" + ",".join("?" for _ in keys) + ") "
            "ON CONFLICT(account_id) DO UPDATE SET " + ",".join(k + "=excluded." + k for k in keys if k != "account_id"),
            tuple(state[k] for k in keys),
        )

    def _capture(self, c: sqlite3.Connection, row: dict, at: int) -> None:
        existing = c.execute("SELECT 1 FROM tbank_live_state WHERE order_id=?", (row["id"],)).fetchone()
        if existing:
            return
        units = credit_units(self.cloud.s, row["amount"])
        terms = json.loads(row["plan_snapshot"] or "{}") if row["plan"] else {}
        if row["plan"] and (not terms or terms.get("id", row["plan"]) != row["plan"]):
            raise CloudError(409, "payment_plan_snapshot", "Не сохранены условия оплаченного пакета")
        previous = self._subscription(c, row["account_id"])
        after = dict(previous)
        review = 0
        if row["plan"]:
            if previous and previous["expires_at"] > at and previous["plan"] != row["plan"]:
                # A delayed bank confirmation cannot lose the money or overwrite another plan.
                review = 1
            else:
                after = {
                    "account_id": row["account_id"],
                    "plan": row["plan"],
                    "name": terms["name"],
                    "price_rub": float(Decimal(row["amount"]) / 100),
                    "discount_pct": int(terms["discount_pct"]),
                    "connect": int(bool(terms["connect"])),
                    "started_at": previous["started_at"] if previous and previous["expires_at"] > at else at,
                    "expires_at": max(at, previous.get("expires_at", 0)) + int(terms.get("days", 30)) * 86400,
                    "paid_uy": units,
                }
                self._write_subscription(c, after, row["account_id"])
        c.execute(
            "INSERT INTO tbank_live_state(order_id,account_id,total_uy,captured_at,plan_snapshot,previous_subscription,entitlement_before,entitlement_after,review_required) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                row["id"],
                row["account_id"],
                units,
                at,
                json.dumps(terms),
                json.dumps(previous),
                json.dumps(previous),
                json.dumps(after),
                review,
            ),
        )
        c.execute("UPDATE accounts SET grant_uy=grant_uy+? WHERE id=?", (units, row["account_id"]))
        c.execute(
            "INSERT INTO ledger(account_id,ts,kind,charged,extra) VALUES(?,?,?,?,?)",
            (
                row["account_id"],
                at,
                "credit",
                0,
                json.dumps({"credit_uy": units, "from": "tbank", "order_id": row["id"], "amount_kopecks": row["amount"]}),
            ),
        )
        c.execute("INSERT INTO tbank_live_events VALUES(?,?,?,?,?,?)", (row["id"], "capture", 0, row["amount"], units, at))
        c.execute("UPDATE tbank_orders SET credited=1,status='CONFIRMED' WHERE id=?", (row["id"],))
        PaymentOutbox.enqueue_settled(c, row["id"], kind="payment_confirmed", now=at)

    def apply(self, row: dict, data: dict, *, reconciled: bool) -> dict:
        if not row["terminal"] or row["terminal"].endswith("DEMO"):
            raise CloudError(409, "payment_demo_isolated", "Тестовые деньги не относятся к рабочему балансу")
        status = data["Status"]
        refund = status in {"REFUNDED", "PARTIAL_REFUNDED"}
        if refund and not reconciled:
            raise CloudError(409, "payment_refund_state", "Для возврата нужна сверка с банком")
        if refund and (
            data.get("Success") is not True
            or (status == "REFUNDED" and data["Amount"] != 0)
            or (status == "PARTIAL_REFUNDED" and not 0 < data["Amount"] < row["amount"])
        ):
            raise CloudError(409, "payment_refund_state", "Банк не подтвердил остаток платежа")
        at = int(time.time())
        with self.db.tx() as c:
            row = dict(c.execute("SELECT * FROM tbank_orders WHERE id=?", (row["id"],)).fetchone())
            if row["refunded"] == row["amount"] or row["status"] in {"REFUNDED", "REVERSED", "CANCELED"}:
                return row
            confirmed = status == "CONFIRMED" and data.get("Success") is True
            if confirmed or refund:
                # Partial/full refund first seen still accounts for capture+reversal atomically.
                self._capture(c, row, at)
                if refund:
                    refunded = row["amount"] - data["Amount"]
                    if refunded > row["refunded"]:
                        state = dict(c.execute("SELECT * FROM tbank_live_state WHERE order_id=?", (row["id"],)).fetchone())
                        total_refund_uy = state["total_uy"] if refunded == row["amount"] else state["total_uy"] * refunded // row["amount"]
                        delta = total_refund_uy - state["refunded_uy"]
                        c.execute("UPDATE accounts SET grant_uy=grant_uy-? WHERE id=?", (delta, row["account_id"]))
                        c.execute(
                            "INSERT INTO ledger(account_id,ts,kind,charged,extra) VALUES(?,?,?,?,?)",
                            (
                                row["account_id"],
                                at,
                                "credit",
                                0,
                                json.dumps(
                                    {"credit_uy": -delta, "from": "tbank_refund", "order_id": row["id"], "refunded_kopecks": refunded}
                                ),
                            ),
                        )
                        c.execute(
                            "INSERT INTO tbank_live_events VALUES(?,?,?,?,?,?)",
                            (row["id"], "refund", refunded, -(refunded - row["refunded"]), -delta, at),
                        )
                        c.execute("UPDATE tbank_live_state SET refunded_uy=? WHERE order_id=?", (total_refund_uy, row["id"]))
                        c.execute("UPDATE tbank_orders SET refunded=?,status=? WHERE id=?", (refunded, status, row["id"]))
                        if row["plan"]:
                            current = self._subscription(c, row["account_id"])
                            expected = json.loads(state["entitlement_after"])
                            if refunded == row["amount"] and current == expected and not state["review_required"]:
                                self._write_subscription(c, json.loads(state["entitlement_before"]), row["account_id"])
                            else:
                                # Never overwrite a later operator action or guess partial-month terms.
                                c.execute("UPDATE tbank_live_state SET review_required=1 WHERE order_id=?", (row["id"],))
                        PaymentOutbox.enqueue_settled(c, row["id"], kind="payment_refunded", now=at)
            elif not row["credited"] and row["status"] not in {"REJECTED", "AUTH_FAIL", "DEADLINE_EXPIRED"}:
                c.execute("UPDATE tbank_orders SET status=? WHERE id=?", (status, row["id"]))
            return dict(c.execute("SELECT * FROM tbank_orders WHERE id=?", (row["id"],)).fetchone())
