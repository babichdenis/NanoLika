"""Operator-only payment diagnostics. This module never enables live collection."""

from __future__ import annotations

import json
import os
import time
from urllib.parse import urlsplit

import httpx
from fastapi import Depends

from .fiscal import FiscalConfig
from .service import CloudError


class SiteCounter:
    """Fetch public aggregates and, separately, key-protected visit details.

    The detail URL (including its key) is only used server-to-server and is never returned
    to the browser. The public stats endpoint must not contain IP addresses or user agents.
    """

    def __init__(self, url: str, transport=None, details_url: str = "", details_key: str = ""):
        self.url = url
        self.transport = transport
        self.details_url = details_url
        self.details_key = details_key
        self.cached: dict | None = None
        self.until = 0.0

    async def get(self) -> dict:
        if self.cached is not None and time.monotonic() < self.until:
            return self.cached
        parsed = urlsplit(self.url)
        out: dict = {"available": False, "error": "not_configured"}
        if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password:
            try:
                async with (
                    httpx.AsyncClient(transport=self.transport, timeout=5, trust_env=False, follow_redirects=False) as client,
                    client.stream("GET", self.url) as response,
                ):
                    response.raise_for_status()
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 131072:
                            raise ValueError("counter_size")
                data = json.loads(raw)
                if not isinstance(data, dict) or data.get("ok") is not True:
                    raise ValueError("counter_data")

                def number(value):
                    return max(0, min(10**12, int(value)))

                out = {
                    "available": True,
                    "ok": True,
                    "fetched_at": int(time.time()),
                    "since": str(data.get("since", ""))[:10],
                    **{k: number(data.get(k, 0)) for k in ("total", "today", "uniq_today")},
                    "days": [
                        {"day": str(r["day"])[:10], "views": number(r["views"]), "uniq": number(r.get("uniq", 0))}
                        for r in data.get("days", [])[-366:]
                    ],
                    "pages": [{"name": str(r["name"])[:200], "hits": number(r["hits"])} for r in data.get("pages", [])[:100]],
                    "refs": [{"name": str(r["name"])[:200], "hits": number(r["hits"])} for r in data.get("refs", [])[:100]],
                    "sources": [{"name": str(r["name"])[:100], "hits": number(r["hits"])} for r in data.get("sources", [])[:100]],
                    "details_available": False,
                }
                details = urlsplit(self.details_url)
                if details.scheme == "https" and details.hostname and not details.username and not details.password:
                    async with httpx.AsyncClient(
                        transport=self.transport, timeout=5, trust_env=False, follow_redirects=False
                    ) as client:
                        headers = {"X-Counter-Key": self.details_key} if self.details_key else {}
                        async with client.stream("GET", self.details_url, headers=headers) as response:
                            response.raise_for_status()
                            raw_details = bytearray()
                            async for chunk in response.aiter_bytes():
                                raw_details.extend(chunk)
                        if len(raw_details) > 2_097_152:
                            raise ValueError("counter_details_size")
                    details_data = json.loads(raw_details)
                    if isinstance(details_data, dict) and details_data.get("ok") is True:
                        visits = []
                        for row in details_data.get("recent", [])[-2000:]:
                            if not isinstance(row, dict):
                                continue
                            visits.append({
                                "id": number(row.get("id", 0)),
                                "visitor": str(row.get("visitor", ""))[:32],
                                "at": number(row.get("at", 0)),
                                "page": str(row.get("page", ""))[:160],
                                "source": str(row.get("source", ""))[:100],
                                "medium": str(row.get("medium", ""))[:100],
                                "campaign": str(row.get("campaign", ""))[:100],
                                "content": str(row.get("content", ""))[:100],
                                "ref": str(row.get("ref", ""))[:120],
                                "ip": str(row.get("ip", ""))[:60],
                                "ua": str(row.get("ua", ""))[:240],
                                "new": bool(row.get("new", False)),
                            })
                        out["visits"] = visits
                        out["visits_total"] = number(details_data.get("visits_total", len(visits)))
                        out["retention_days"] = number(details_data.get("retention_days", 0))
                        out["details_available"] = True
            except (httpx.HTTPError, ValueError, TypeError, KeyError):
                # A private details endpoint may be temporarily unreachable. Keep public
                # aggregates available but never leak the URL/key or partial detail data.
                if out.get("available"):
                    out["details_available"] = False
                    out.pop("visits", None)
                    out.pop("visits_total", None)
                else:
                    out = {"available": False, "error": "counter_unavailable"}
        self.cached = out
        self.until = time.monotonic() + (60 if out["available"] else 15)
        return out


def readiness(cloud, payments) -> dict:
    """Facts about the running process, not promises inferred from a terminal key."""
    retail = any(getattr(m, "sells_at_own_rate", lambda: False)() for m in cloud.s.models if m.kind == "chat")
    plans = payments.plans()
    provider_name = "ЮKassa" if getattr(payments, "provider", "tbank") == "yookassa" else "Т-Банк"
    test_mode = bool(getattr(payments, "test_mode", payments.terminal.endswith("DEMO")))
    checks = [
        {
            "id": "demo_terminal",
            "ok": payments.terminal.endswith("DEMO") and bool(payments.password),
            "label": f"Тестовое подключение {provider_name} настроено",
            "label_en": f"{provider_name} test connection configured",
        },
        {
            "id": "live_settlement",
            "ok": False,
            "label": "Боевые зачисления и возвраты ещё не реализованы",
            "label_en": "Live settlement and refunds are not implemented",
        },
        {
            "id": "fiscal_receipts",
            "ok": False,
            "label": "Нет фискализации и доставки чека клиенту",
            "label_en": "Fiscal receipts and delivery to customers are not connected",
        },
        {
            "id": "retail_pricing",
            "ok": retail,
            "label": "Розничные ставки отделены от себестоимости",
            "label_en": "Retail prices are separate from provider costs",
        },
        {
            "id": "paid_entitlements",
            "ok": hasattr(cloud, "plan_state") and bool(getattr(cloud.s, "connect_requires_plan", False)),
            "label": "Права связи проверяются по оплаченному тарифу",
            "label_en": "Cross-device access is enforced by paid plan",
        },
        {
            "id": "payment_notices",
            "ok": False,
            "label": "События оплаты ещё не подключены к письмам клиенту",
            "label_en": "Payment events are not wired to customer emails",
        },
        {
            "id": "scheduled_reconciliation",
            "ok": bool(getattr(payments, "jobs_state", {}).get("running")),
            "label": "Фоновая сверка с банком"
            if getattr(payments, "jobs_state", {}).get("running")
            else "Фоновая сверка с банком ещё не подключена",
            "label_en": "Scheduled bank reconciliation is not connected",
        },
    ]
    return {
        "live_enabled": False,
        "ready_for_first_live_payment": False,
        "mode": "demo" if test_mode else "blocked",
        "checks": checks,
        "plans": plans,
        "site_checkout": "placeholder",
        "fiscal_preparation": FiscalConfig.from_env().diagnostics(),
        "live_accounting_prepared": hasattr(payments, "live_billing"),
        "reconciliation": getattr(payments, "jobs_state", {"running": False}),
        "payment_notice_preparation": {
            "transactional_hook_prepared": True,
            "retry_dispatcher_prepared": True,
            "live_settlement_connected": False,
            "idempotent_mail_receiver_verified": False,
            "scheduled_dispatch_enabled": False,
        },
        "flow": {
            "site": "https://nanolika.ru/pricing.html",
            "checkout": payments.public_base + "/payments/checkout",
            "bank": "Т-Банк",
            "renewal": "manual",
            "demo_isolated": True,
        },
        "billing_models": [
            {
                "id": m.id,
                "kind": m.kind,
                "cost_in_cny": m.price_in,
                "cost_out_cny": m.price_out,
                "sell_in_rub": getattr(m, "sell_in", 0),
                "sell_cached_in_rub": getattr(m, "sell_cached_in", 0),
                "sell_out_rub": getattr(m, "sell_out", 0),
            }
            for m in cloud.s.models
            if m.kind == "chat"
        ],
        "display_rate": {"rub_per_usd": cloud.s.rub_per_usd, "usd_cny": cloud.s.usd_cny, "topup_fee_pct": cloud.s.topup_fee_pct},
    }


def install_payment_audit(app, cloud, payments, admin_dep):
    counter = SiteCounter(
        os.getenv("SITE_COUNTER_URL", ""),
        details_url=os.getenv("SITE_COUNTER_DETAILS_URL", ""),
        details_key=os.getenv("SITE_COUNTER_DETAILS_KEY", ""),
    )
    app.state.site_counter = counter

    @app.get("/v1/admin/site-counter", dependencies=[Depends(admin_dep)])
    async def site_counter():
        return await counter.get()

    @app.get("/v1/admin/payments/readiness", dependencies=[Depends(admin_dep)])
    async def payment_readiness():
        return readiness(cloud, payments)

    @app.get("/v1/admin/payments/{order_id}", dependencies=[Depends(admin_dep)])
    async def payment_detail(order_id: str):
        row = payments.order(order_id)
        with payments.db.tx() as c:
            events = [
                dict(r)
                for r in c.execute("SELECT id,status,ts,amount_delta FROM tbank_demo_events WHERE order_id=? ORDER BY id", (order_id,))
            ]
        net = max(0, row["amount"] - row["refunded"]) if row["credited"] else 0
        return {
            "order": payments.admin_view(row),
            "events": events,
            "event_total_kopecks": sum(r["amount_delta"] for r in events),
            "ledger_matches": sum(r["amount_delta"] for r in events) == net,
            "receipt_status": "not_applicable_demo" if row["terminal"].endswith("DEMO") else "not_connected",
        }

    @app.post("/v1/admin/payments/{order_id}/reconcile", dependencies=[Depends(admin_dep)])
    async def reconcile(order_id: str):
        row = payments.order(order_id)
        if row["terminal"] != payments.terminal or not row["payment_id"]:
            raise CloudError(409, "payment_reconcile_unavailable", "Не найден платёж текущего терминала для сверки")
        # Same per-order throttle as customer status. No new payment and no refund is initiated.
        now = int(time.time())
        with payments.db.tx() as c:
            claimed = c.execute("UPDATE tbank_orders SET checked_at=? WHERE id=? AND checked_at<=?", (now, order_id, now - 5)).rowcount
        if not claimed:
            raise CloudError(429, "payment_reconcile_wait", "Повторите сверку через пять секунд")
        data = await payments.bank("GetState", {"PaymentId": row["payment_id"]})
        return payments.admin_view(payments.apply(data, reconciled=True))
