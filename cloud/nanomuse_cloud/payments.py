"""T-Bank DEMO checkout. Test credits and refund reversals never touch real grants or subscriptions.

Only the bank's signed CONFIRMED event or an authenticated GetState response settles an
order. Browser return URLs are navigation, never evidence of payment.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import ssl
import time
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from .live_billing import LiveBilling
from .service import Caller, Cloud, CloudError

if TYPE_CHECKING:
    from .fiscal import FiscalConfig

PLANS = {
    "lika": {"name": "Лика", "amount": 59000, "discount": 20, "connect": False},
    "plus": {"name": "Лика+", "amount": 149000, "discount": 20, "connect": True},
    "team": {"name": "Лика Команда", "amount": 390000, "discount": 30, "connect": True},
}
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS tbank_orders (
    id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id),
    idempotency TEXT NOT NULL, plan TEXT NOT NULL, amount INTEGER NOT NULL,
    payment_id TEXT UNIQUE, payment_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'CREATING', credited INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL, checked_at INTEGER NOT NULL DEFAULT 0,
    terms_version TEXT NOT NULL DEFAULT '2026-10-22',
    UNIQUE(account_id,idempotency))""",
    """CREATE TABLE IF NOT EXISTS tbank_demo_wallets (
    account_id TEXT PRIMARY KEY REFERENCES accounts(id), balance INTEGER NOT NULL DEFAULT 0,
    plan TEXT NOT NULL DEFAULT '', expires_at INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS tbank_demo_events (
    id INTEGER PRIMARY KEY, order_id TEXT NOT NULL REFERENCES tbank_orders(id),
    status TEXT NOT NULL, ts INTEGER NOT NULL)""",
)


def token(payload: dict, password: str) -> str:
    values = {k: v for k, v in payload.items() if k != "Token" and v is not None and not isinstance(v, (dict, list))}
    values["Password"] = password

    def text(value):
        return str(value).lower() if isinstance(value, bool) else str(value)

    return hashlib.sha256("".join(text(values[k]) for k in sorted(values)).encode()).hexdigest()


def cents(value: str) -> int:
    try:
        amount = Decimal(value)
        n = amount * 100
        if not amount.is_finite() or n != n.to_integral_value() or not 1 <= n <= 10_000_000:
            raise ValueError
        return int(n)
    except (InvalidOperation, ValueError):
        raise CloudError(400, "payment_amount", "Сумма от 0,01 до 100 000 ₽, не больше двух знаков после запятой") from None


class OrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accept_terms: bool = False
    plan: str = Field(default="", max_length=16)
    amount: str = Field(default="", max_length=16)
    idempotency_key: str = Field(min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")


class Payments:
    def __init__(self, cloud: Cloud, terminal: str, password: str, public_base: str, transport=None):
        self.cloud, self.db = cloud, cloud.db
        self.terminal, self.password, self.public_base = terminal, password, public_base.rstrip("/")
        self.transport = transport
        with self.db.tx() as c:
            for sql in SCHEMA:
                c.execute(sql)
            columns = {row[1] for row in c.execute("PRAGMA table_info(tbank_orders)")}
            if "refunded" not in columns:
                c.execute("ALTER TABLE tbank_orders ADD COLUMN refunded INTEGER NOT NULL DEFAULT 0")
            if "terminal" not in columns:
                c.execute("ALTER TABLE tbank_orders ADD COLUMN terminal TEXT NOT NULL DEFAULT ''")
            if "plan_snapshot" not in columns:
                c.execute("ALTER TABLE tbank_orders ADD COLUMN plan_snapshot TEXT NOT NULL DEFAULT ''")
            if "plan_granted_at" not in columns:
                c.execute("ALTER TABLE tbank_orders ADD COLUMN plan_granted_at INTEGER NOT NULL DEFAULT 0")
                c.execute("ALTER TABLE tbank_orders ADD COLUMN plan_granted_by TEXT NOT NULL DEFAULT ''")
            if self.terminal.endswith("DEMO"):
                c.execute("UPDATE tbank_orders SET terminal=? WHERE terminal=''", (self.terminal,))
            events = {row[1] for row in c.execute("PRAGMA table_info(tbank_demo_events)")}
            if "amount_delta" not in events:
                c.execute("ALTER TABLE tbank_demo_events ADD COLUMN amount_delta INTEGER NOT NULL DEFAULT 0")
                c.execute(
                    "UPDATE tbank_demo_events SET amount_delta=(SELECT amount FROM tbank_orders WHERE id=order_id) WHERE status='CONFIRMED'"
                )

        self.live_billing = LiveBilling(cloud)

    def ready(self):
        # Intentionally no production enable switch: fiscal receipts, live account settlement and
        # commercial refund policy need their own release. Changing a terminal must not enable real charges.
        if not self.terminal.endswith("DEMO") or not self.password or not self.public_base.startswith("https://"):
            raise CloudError(503, "payment_unavailable", "Тестовая оплата пока недоступна")

    async def bank(self, method: str, payload: dict) -> dict:
        self.ready()
        body = dict(payload, TerminalKey=self.terminal)
        body["Token"] = token(body, self.password)
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=20,
                trust_env=False,
                follow_redirects=False,
                verify=ssl.create_default_context(cafile=str(Path(__file__).parent / "certificates" / "tbank-root.pem")),
            ) as client:
                response = await client.post("https://securepay.tinkoff.ru/v2/" + method, json=body)
                response.raise_for_status()
                result = response.json()
            if not isinstance(result, dict) or result.get("Success") is not True:
                raise ValueError
            return result
        except (httpx.HTTPError, ValueError):
            # No response body or signed request in logs: both can contain private details.
            raise CloudError(502, "payment_bank", "Банк не подтвердил операцию. Проверьте статус заказа перед новой попыткой") from None

    def plans(self) -> dict:
        """Меню тарифов релея — один источник и для кассы, и для выдачи прав.

        Раньше касса продавала свой захардкоженный список, а релей начислял по меню из
        `PLANS`: если идентификаторы не совпадали, человек платил и не получал тариф.
        """
        menu = {row["id"]: row for row in self.cloud.plan_menu()} if hasattr(self.cloud, "plan_menu") else {}
        if menu:
            return menu
        return {
            key: {
                "id": key,
                "name": row["name"],
                "price_rub": row["amount"] / 100,
                "discount_pct": row["discount"],
                "connect": row["connect"],
                "days": 30,
            }
            for key, row in PLANS.items()
        }

    def orders_page(
        self, *, days: int = 30, status: str = "", q: str = "", limit: int = 100, before: int = 0, before_id: str = "", mode: str = "all"
    ) -> dict:
        """Payment attempts and captured money are different figures. DEMO is never revenue."""
        if mode not in {"all", "demo", "live"}:
            raise CloudError(400, "payment_filter", "Неизвестный режим платежей")
        limit = max(1, min(int(limit), 500))
        days = max(1, min(int(days), 3650))
        where = ["o.created_at >= ?"]
        args: list = [int(time.time()) - days * 86400]
        if status:
            where.append("o.status = ?")
            args.append(status)
        if q:
            where.append("(a.hint LIKE ? OR o.id LIKE ? OR o.payment_id LIKE ?)")
            args.extend([f"%{q[:100]}%"] * 3)
        if mode != "all":
            where.append("o.terminal LIKE '%DEMO'" if mode == "demo" else "(o.terminal<>'' AND o.terminal NOT LIKE '%DEMO')")
        clause = " AND ".join(where)
        table = "FROM tbank_orders o LEFT JOIN accounts a ON a.id=o.account_id"
        grouping = "CASE WHEN o.terminal LIKE '%DEMO' THEN 'demo' WHEN o.terminal<>'' THEN 'live' ELSE 'unknown' END"
        with self.db.tx() as c:
            # Aggregate in SQLite and paginate only the rows returned to the browser.
            aggregate = [
                dict(r)
                for r in c.execute(
                    f"SELECT {grouping} AS mode, COUNT(*) AS orders, COALESCE(SUM(o.amount),0) AS requested, "
                    "COALESCE(SUM(CASE WHEN o.credited=1 THEN o.amount ELSE 0 END),0) AS captured, "
                    "COALESCE(SUM(o.refunded),0) AS refunded, "
                    "COALESCE(SUM(CASE WHEN o.credited=1 THEN o.amount-o.refunded ELSE 0 END),0) AS net "
                    f"{table} WHERE {clause} GROUP BY mode",
                    args,
                )
            ]
            statuses = [
                dict(r)
                for r in c.execute(
                    f"SELECT o.status, COUNT(*) AS count, SUM(o.amount) AS amount {table} WHERE {clause} GROUP BY o.status", args
                )
            ]
            row_where, row_args = clause, list(args)
            if before:
                row_where += " AND (o.created_at<? OR (o.created_at=? AND o.id<?))"
                row_args.extend([before, before, before_id])
            rows = [
                dict(r)
                for r in c.execute(
                    f"SELECT o.*, a.hint {table} WHERE {row_where} ORDER BY o.created_at DESC,o.id DESC LIMIT ?", (*row_args, limit + 1)
                )
            ]
        split = {kind: {"orders": 0, "requested": 0, "captured": 0, "refunded": 0, "net": 0} for kind in ("live", "demo", "unknown")}
        for row in aggregate:
            split[row.pop("mode")] = row
        totals = {
            "orders": sum(x["orders"] for x in split.values()),
            "amount": sum(x["requested"] for x in split.values()),
            "refunded": sum(x["refunded"] for x in split.values()),
            "by_status": {r["status"]: {"count": r["count"], "amount": r["amount"]} for r in statuses},
        }
        has_more = len(rows) > limit
        rows = rows[:limit]
        return {
            "days": days,
            "test_mode": True,
            "summary": {**totals, **split},
            "orders": [self.admin_view(r) for r in rows],
            "next_before": rows[-1]["created_at"] if has_more else 0,
            "next_before_id": rows[-1]["id"] if has_more else "",
        }

    def admin_view(self, row: dict) -> dict:
        view = self.view(row)
        view.pop("payment_url", None)
        return {
            **view,
            "account_id": row["account_id"],
            "hint": row.get("hint", ""),
            "payment_id": row["payment_id"],
            "created_at": row["created_at"],
            "checked_at": row["checked_at"],
            "terms_version": row["terms_version"],
            "plan_granted_at": row.get("plan_granted_at", 0),
            "test_mode": row["terminal"].endswith("DEMO"),
            "mode": "demo" if row["terminal"].endswith("DEMO") else "live" if row["terminal"] else "unknown",
        }

    def grant_plan(self, order_id: str, actor: str = "console") -> dict:
        # A DEMO credit must never create real spendable money or a real subscription.
        self.order(order_id)
        raise CloudError(409, "payment_demo_isolated", "Тестовый платёж не выдаёт рабочий тариф")

    def order(self, order_id: str, account_id: str | None = None) -> dict:
        with self.db.tx() as c:
            row = c.execute("SELECT * FROM tbank_orders WHERE id=?", (order_id,)).fetchone()
        if row is None or (account_id is not None and row["account_id"] != account_id):
            raise CloudError(404, "payment_missing", "Заказ не найден")
        return dict(row)

    async def create(self, caller: Caller, request: OrderRequest, *, fiscal: FiscalConfig | None = None, receipt_email: str = "") -> dict:
        self.ready()
        if not request.accept_terms:
            raise CloudError(400, "payment_terms", "Подтвердите ознакомление с условиями тестового оформления")
        menu = self.plans()
        if request.plan == "plus" and "plus" not in menu and "lika-plus" in menu:
            request = request.model_copy(update={"plan": "lika-plus"})
        if request.plan:
            chosen = menu.get(request.plan)
            if chosen is None or request.amount:
                raise CloudError(400, "payment_plan", "Выберите известный пакет без произвольной цены")
            amount = cents(str(chosen["price_rub"]))
        else:
            amount = cents(request.amount)
        # Explicit server-side policy only. DEMO-only ready() remains mandatory.
        # A fixture policy used by an operator test is never persisted as merchant settings.
        receipt = (
            fiscal.prepare(kind="plan" if request.plan else "topup", amount_kopecks=amount, customer_email=receipt_email)
            if fiscal is not None
            else None
        )
        with self.db.tx() as c:
            existing = c.execute(
                "SELECT * FROM tbank_orders WHERE account_id=? AND idempotency=?", (caller.account_id, request.idempotency_key)
            ).fetchone()
            if existing is not None:
                if existing["plan"] != request.plan or existing["amount"] != amount:
                    raise CloudError(409, "payment_conflict", "Этот запрос уже использован для другого заказа")
                if not existing["payment_url"]:
                    raise CloudError(409, "payment_pending", "Заказ ещё проверяется. Не создавайте повторный платёж")
                return self.view(dict(existing))
            if not self.terminal.endswith("DEMO"):
                self.live_billing.validate_plan_purchase(c, caller.account_id, request.plan, now=int(time.time()))
            count = c.execute(
                "SELECT COUNT(*) FROM tbank_orders WHERE account_id=? AND created_at>?", (caller.account_id, int(time.time()) - 3600)
            ).fetchone()[0]
            if count >= 10:
                raise CloudError(429, "payment_rate", "Слишком много заказов. Попробуйте позднее")
            order_id = uuid.uuid4().hex
            c.execute(
                "INSERT INTO tbank_orders(id,account_id,idempotency,plan,amount,created_at,terminal,plan_snapshot) VALUES(?,?,?,?,?,?,?,?)",
                (
                    order_id,
                    caller.account_id,
                    request.idempotency_key,
                    request.plan,
                    amount,
                    int(time.time()),
                    self.terminal,
                    json.dumps(menu.get(request.plan, {})),
                ),
            )
        result = await self.bank(
            "Init",
            {
                "OrderId": order_id,
                "Amount": amount,
                "Description": "nanoLika DEMO: " + (menu[request.plan]["name"] if request.plan else "пополнение"),
                "NotificationURL": self.public_base + "/v1/payments/tbank/notify",
                "SuccessURL": self.public_base + "/payments/checkout?order=" + order_id,
                "FailURL": self.public_base + "/payments/checkout?order=" + order_id,
                **({"Receipt": receipt} if receipt is not None else {}),
            },
        )
        if str(result.get("OrderId")) != order_id or type(result.get("Amount")) is not int or result["Amount"] != amount:
            raise CloudError(502, "payment_mismatch", "Банк вернул несовпадающие данные заказа")
        payment_id = str(result.get("PaymentId", ""))
        url = str(result.get("PaymentURL", ""))
        parsed = urlparse(url)
        if (
            not payment_id.isdigit()
            or len(payment_id) > 20
            or parsed.scheme != "https"
            or parsed.hostname
            not in {
                "pay.tbank-online.com",
                "securepayments.tinkoff.ru",
                "securepayments.tbank.ru",
                "securepay.tinkoff.ru",
                "securepay.tbank.ru",
            }
            or parsed.username
            or parsed.port not in (None, 443)
        ):
            raise CloudError(502, "payment_url", "Банк не вернул допустимую страницу оплаты")
        with self.db.tx() as c:
            c.execute(
                "UPDATE tbank_orders SET payment_id=?,payment_url=?,status=CASE WHEN status='CREATING' THEN 'NEW' ELSE status END WHERE id=? AND (payment_id IS NULL OR payment_id=?)",
                (payment_id, url, order_id, payment_id),
            )
        return self.view(self.order(order_id))

    def validate_event(self, data: dict, *, signed: bool = False) -> dict:
        if signed:
            supplied = data.get("Token")
            if not isinstance(supplied, str) or not hmac.compare_digest(supplied, token(data, self.password)):
                raise CloudError(403, "payment_signature", "Неверная подпись уведомления")
        if data.get("TerminalKey") != self.terminal:
            raise CloudError(403, "payment_terminal", "Другой терминал")
        order_id = str(data.get("OrderId", ""))
        payment_id = str(data.get("PaymentId", ""))
        status = str(data.get("Status", ""))
        if not payment_id.isdigit() or len(status) > 40:
            raise CloudError(400, "payment_event", "Некорректное уведомление")
        row = self.order(order_id)
        refund = status in {"REFUNDED", "PARTIAL_REFUNDED"}
        amount = data.get("Amount")
        amount_ok = type(amount) is int and (0 <= amount <= row["amount"] if refund else amount == row["amount"])
        if row["terminal"] != self.terminal or not amount_ok or (row["payment_id"] is not None and row["payment_id"] != payment_id):
            raise CloudError(409, "payment_mismatch", "Данные платежа не совпали")
        return row

    def _rebuild_plan(self, c, account_id: str) -> None:
        # Replay only funded packages in their original confirmation order and at their
        # original times. A refund never starts a fresh month or removes an unrelated plan.
        rows = c.execute(
            "SELECT o.plan, o.plan_snapshot, MIN(e.ts) AS confirmed_at, MIN(e.id) AS seq "
            "FROM tbank_orders o JOIN tbank_demo_events e ON e.order_id=o.id "
            "AND e.status='CONFIRMED' WHERE o.account_id=? AND o.credited=1 "
            "AND o.refunded<o.amount AND o.plan<>'' GROUP BY o.id ORDER BY seq",
            (account_id,),
        ).fetchall()
        plan, expiry = "", 0
        for row in rows:
            start = max(row["confirmed_at"], expiry) if plan == row["plan"] else row["confirmed_at"]
            snapshot = json.loads(row["plan_snapshot"] or "{}")
            plan, expiry = row["plan"], start + int(snapshot.get("days", 30)) * 86400
        c.execute("UPDATE tbank_demo_wallets SET plan=?,expires_at=? WHERE account_id=?", (plan, expiry, account_id))

    def apply(self, data: dict, *, signed: bool = False, reconciled: bool = False) -> dict:
        validated = self.validate_event(data, signed=signed)
        if not self.terminal.endswith("DEMO"):
            # Live collection remains gated until the fiscal and delivery release is ready.
            self.ready()
            return self.live_billing.apply(validated, data, reconciled=reconciled)
        order_id, payment_id, status = validated["id"], str(data["PaymentId"]), str(data["Status"])
        refund = status in {"REFUNDED", "PARTIAL_REFUNDED"}
        if refund and not reconciled:
            # A webhook's Amount can describe one operation. Only GetState supplies the
            # current remaining amount, needed for monotonic cumulative refund accounting.
            raise CloudError(409, "payment_refund_state", "Для возврата нужна сверка с банком")
        if refund and (
            data.get("Success") is not True
            or (status == "REFUNDED" and data["Amount"] != 0)
            or (status == "PARTIAL_REFUNDED" and not 0 < data["Amount"] < validated["amount"])
        ):
            raise CloudError(409, "payment_refund_state", "Банк не подтвердил сумму возврата")
        with self.db.tx() as c:
            row = c.execute("SELECT * FROM tbank_orders WHERE id=?", (order_id,)).fetchone()
            # Full refunds are final. Late CONFIRMED, pending states, and duplicate events
            # cannot restore money or subscription rights.
            if row["refunded"] == row["amount"] or row["status"] in {"REFUNDED", "REVERSED", "CANCELED"}:
                return dict(row)
            confirmed = status == "CONFIRMED" and data.get("Success") is True
            if confirmed or refund:
                now = int(time.time())
                c.execute("INSERT OR IGNORE INTO tbank_demo_wallets(account_id) VALUES(?)", (row["account_id"],))
                credit = bool(row["credited"])
                # A current partial refund also proves the original capture. This repairs
                # a missed confirmation without losing the remaining funded amount.
                if not credit and (confirmed or status == "PARTIAL_REFUNDED"):
                    c.execute("UPDATE tbank_demo_wallets SET balance=balance+? WHERE account_id=?", (row["amount"], row["account_id"]))
                    c.execute("UPDATE tbank_orders SET credited=1,payment_id=? WHERE id=?", (payment_id, order_id))
                    at = now if confirmed else row["created_at"]
                    c.execute(
                        "INSERT INTO tbank_demo_events(order_id,status,ts,amount_delta) VALUES(?,?,?,?)",
                        (order_id, "CONFIRMED", at, row["amount"]),
                    )
                    credit = True
                if refund:
                    refunded = row["amount"] - data["Amount"]
                    if refunded > row["refunded"]:
                        delta = refunded - row["refunded"] if credit else 0
                        # Preserve a negative balance if funds were already consumed. Never
                        # hide debt by clamping it or taking another account's balance.
                        c.execute("UPDATE tbank_demo_wallets SET balance=balance-? WHERE account_id=?", (delta, row["account_id"]))
                        c.execute(
                            "UPDATE tbank_orders SET refunded=?,status=?,payment_id=? WHERE id=?", (refunded, status, payment_id, order_id)
                        )
                        c.execute(
                            "INSERT INTO tbank_demo_events(order_id,status,ts,amount_delta) VALUES(?,?,?,?)",
                            (order_id, status, now, -delta),
                        )
                elif not row["refunded"]:
                    c.execute("UPDATE tbank_orders SET status='CONFIRMED',payment_id=? WHERE id=?", (payment_id, order_id))
                self._rebuild_plan(c, row["account_id"])
            elif not row["credited"] and row["status"] not in {"REJECTED", "AUTH_FAIL", "DEADLINE_EXPIRED"}:
                c.execute("UPDATE tbank_orders SET status=?,payment_id=? WHERE id=?", (status, payment_id, order_id))
        return self.order(order_id)

    def view(self, row: dict) -> dict:
        net = max(0, row["amount"] - row["refunded"]) if row["credited"] else 0
        return {
            "order_id": row["id"],
            "plan": row["plan"],
            "plan_name": json.loads(row.get("plan_snapshot") or "{}").get("name", row["plan"]),
            "amount_kopecks": row["amount"],
            "status": row["status"],
            "payment_url": row["payment_url"],
            "credited": net > 0,
            "was_credited": bool(row["credited"]),
            "refunded_amount_kopecks": row["refunded"],
            "net_credited_amount_kopecks": net,
            "test_mode": row["terminal"].endswith("DEMO"),
        }

    def wallet(self, caller: Caller) -> dict:
        with self.db.tx() as c:
            row = c.execute("SELECT * FROM tbank_demo_wallets WHERE account_id=?", (caller.account_id,)).fetchone()
        return {
            "test_mode": True,
            "balance_kopecks": row["balance"] if row else 0,
            "plan": row["plan"] if row else "",
            "expires_at": row["expires_at"] if row else 0,
        }


def install(app, cloud: Cloud, caller_dep, *, payments: Payments | None = None, admin_dep=None):
    p = payments or Payments(
        cloud, os.getenv("TBANK_TERMINAL_KEY", ""), os.getenv("TBANK_PASSWORD", ""), os.getenv("TBANK_PUBLIC_BASE", "")
    )
    caller_auth = Depends(caller_dep)
    app.state.payments = p
    router = APIRouter()
    static = Path(__file__).parent / "console" / "payments"

    @router.get("/payments/checkout")
    async def checkout():
        return FileResponse(
            static / "index.html",
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            },
        )

    @router.get("/payments/checkout.js")
    async def checkout_js():
        return FileResponse(static / "checkout.js", media_type="application/javascript")

    @router.get("/payments/checkout.css")
    async def checkout_css():
        return FileResponse(static / "checkout.css", media_type="text/css")

    @router.get("/v1/payments/config")
    async def config():
        p.ready()
        return {
            "test_mode": True,
            "plans": {
                key: {
                    "name": r["name"],
                    "amount": cents(str(r["price_rub"])),
                    "discount": r["discount_pct"],
                    "connect": r["connect"],
                    "days": r.get("days", 30),
                }
                for key, r in p.plans().items()
            },
            "merchant": {
                "name": os.getenv("PAYMENT_MERCHANT_NAME", ""),
                "inn": os.getenv("PAYMENT_MERCHANT_INN", ""),
                "ogrnip": os.getenv("PAYMENT_MERCHANT_OGRNIP", ""),
            },
        }

    @router.post("/v1/payments/orders")
    async def create(body: OrderRequest, caller: Caller = caller_auth):
        return await p.create(caller, body)

    @router.get("/v1/payments/wallet")
    async def wallet(caller: Caller = caller_auth):
        return p.wallet(caller)

    @router.get("/v1/payments/orders/{order_id}")
    async def state(order_id: str, caller: Caller = caller_auth):
        row = p.order(order_id, caller.account_id)
        if row["payment_id"] and row["status"] not in {"REFUNDED", "REVERSED", "CANCELED", "REJECTED", "AUTH_FAIL", "DEADLINE_EXPIRED"}:
            now = int(time.time())
            with p.db.tx() as c:
                changed = c.execute("UPDATE tbank_orders SET checked_at=? WHERE id=? AND checked_at<=?", (now, order_id, now - 5)).rowcount
            if changed:
                data = await p.bank("GetState", {"PaymentId": row["payment_id"]})
                row = p.apply(data, reconciled=True)
        return p.view(row)

    @router.post("/v1/payments/tbank/notify")
    async def notify(request: Request):
        p.ready()
        raw = await request.body()
        if len(raw) > 32768:
            raise CloudError(413, "payment_event", "Слишком большое уведомление")
        try:
            data = json.loads(raw)
        except ValueError:
            raise CloudError(400, "payment_event", "Некорректное уведомление") from None
        if not isinstance(data, dict):
            raise CloudError(400, "payment_event", "Некорректное уведомление")
        if data.get("Status") in {"REFUNDED", "PARTIAL_REFUNDED"}:
            row = p.validate_event(data, signed=True)
            state = await p.bank("GetState", {"PaymentId": row["payment_id"] or data["PaymentId"]})
            if state.get("Status") not in {"REFUNDED", "PARTIAL_REFUNDED"}:
                raise CloudError(409, "payment_refund_pending", "Возврат ещё сверяется с банком")
            p.apply(state, reconciled=True)
        else:
            p.apply(data, signed=True)
        return PlainTextResponse("OK")

    if admin_dep is not None:

        @router.post("/v1/admin/payments/{order_id}/plan", dependencies=[Depends(admin_dep)])
        async def admin_grant_plan(order_id: str, request: Request) -> dict:
            """Выдать тариф по оплаченному заказу. Повторная выдача — 409."""
            actor = (request.headers.get("x-admin-actor") or "console").strip() or "console"
            return p.grant_plan(order_id, actor)

        @router.get("/v1/admin/payments", dependencies=[Depends(admin_dep)])
        async def admin_payments(
            days: int = 30, status: str = "", q: str = "", limit: int = 100, before: int = 0, before_id: str = "", mode: str = "all"
        ) -> dict:
            """Заказы кассы для оператора: только чтение, движения денег нет."""
            return p.orders_page(days=days, status=status, q=q, limit=limit, before=before, before_id=before_id, mode=mode)

    app.include_router(router)
    from .payment_jobs import install_payment_jobs

    install_payment_jobs(app, p)
    if admin_dep is not None:
        from .payment_audit import install_payment_audit

        install_payment_audit(app, cloud, p, admin_dep)
