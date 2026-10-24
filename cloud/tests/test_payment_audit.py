"""Regression tests for operator money figures, test users, and private counter access."""

import json

import httpx
from test_accounts import auth
from test_payments import notice, setup

from nanomuse_cloud.payment_audit import SiteCounter


async def test_test_users_are_explicit_not_guessed_from_email(monkeypatch):
    _, client, user, p, _, _ = await setup(monkeypatch)
    with p.db.tx() as c:
        c.execute("UPDATE accounts SET is_test=1 WHERE id=?", (user["account"]["id"],))
    response = await client.get("/v1/admin/overview", headers={"X-Admin-Token": "admin"})
    counts = response.json()["accounts"]
    assert counts["total"] == 1 and counts["customers"] == 0 and counts["test_accounts"] == 1
    await client.aclose()


async def test_summary_does_not_count_attempts_as_revenue_and_cursor_is_stable(monkeypatch):
    _, client, user, p, _, _ = await setup(monkeypatch)
    rows = []
    for n in range(3):
        rows.append(
            (
                await client.post(
                    "/v1/payments/orders",
                    headers=auth(user["api_key"]),
                    json={"amount": "200", "accept_terms": True, "idempotency_key": f"summary_fixture_{n}"},
                )
            ).json()
        )
    await client.post("/v1/payments/tbank/notify", json=notice(p, rows[0]))
    await client.post("/v1/payments/tbank/notify", json=notice(p, rows[1], Status="REJECTED", Success=False))
    with p.db.tx() as c:
        c.execute("UPDATE tbank_orders SET created_at=CAST(strftime('%s','now') AS INTEGER)")
    page1 = p.orders_page(limit=2)
    page2 = p.orders_page(limit=2, before=page1["next_before"], before_id=page1["next_before_id"])
    assert len({x["order_id"] for x in page1["orders"] + page2["orders"]}) == 3
    assert page1["summary"] == page2["summary"]
    assert page1["summary"]["demo"]["requested"] == 60000
    assert page1["summary"]["demo"]["captured"] == 20000
    assert page1["summary"]["live"]["captured"] == 0
    assert p.orders_page(mode="live")["orders"] == []
    await client.aclose()


async def test_operator_routes_authentication_readiness_and_bank_reconciliation(monkeypatch):
    _, client, user, p, _, bank = await setup(monkeypatch)
    for path in ["/v1/admin/payments/readiness", "/v1/admin/site-counter"]:
        assert (await client.get(path)).status_code == 401
    ready = (await client.get("/v1/admin/payments/readiness", headers={"X-Admin-Token": "admin"})).json()
    assert ready["ready_for_first_live_payment"] is False
    assert p.password not in json.dumps(ready)
    row = (
        await client.post(
            "/v1/payments/orders",
            headers=auth(user["api_key"]),
            json={"amount": "200", "accept_terms": True, "idempotency_key": "audit_bank_fixture"},
        )
    ).json()
    pid = p.order(row["order_id"])["payment_id"]
    bank[pid]["Status"] = "CONFIRMED"
    path = f"/v1/admin/payments/{row['order_id']}/reconcile"
    assert (await client.post(path)).status_code == 401
    assert (await client.post(path, headers={"X-Admin-Token": "admin"})).json()["credited"] is True
    assert (await client.post(path, headers={"X-Admin-Token": "admin"})).status_code == 429
    detail = (await client.get(f"/v1/admin/payments/{row['order_id']}", headers={"X-Admin-Token": "admin"})).json()
    assert detail["ledger_matches"] and detail["event_total_kopecks"] == 20000
    assert "payment_url" not in detail["order"]
    await client.aclose()


async def test_checkout_config_and_creation_use_the_same_menu(monkeypatch):
    _, client, user, p, _, _ = await setup(monkeypatch)
    config = (await client.get("/v1/payments/config")).json()
    assert set(config["plans"]) == set(p.plans())
    alias = (
        await client.post(
            "/v1/payments/orders",
            headers=auth(user["api_key"]),
            json={"plan": "plus", "accept_terms": True, "idempotency_key": "legacy_plus_fixture"},
        )
    ).json()
    assert alias["amount_kopecks"] == 149000 and alias["plan"] == "lika-plus"
    await client.aclose()


async def test_counter_is_cached_and_drops_visit_details_and_credentials():
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "total": 12,
                "today": 3,
                "uniq_today": 2,
                "days": [],
                "pages": [],
                "refs": [],
                "recent": [{"id": 1}],
                "key": "private",
            },
        )

    counter = SiteCounter("https://counter.example.test/?key=private", transport=httpx.MockTransport(upstream))
    first = await counter.get()
    assert first["available"] and first["total"] == 12
    assert "private" not in json.dumps(first) and "recent" not in first
    assert await counter.get() == first and len(calls) == 1


async def test_counter_failure_is_cached_to_avoid_redraw_request_loop():
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(503)

    counter = SiteCounter("https://counter.example.test/", transport=httpx.MockTransport(upstream))
    assert (await counter.get())["available"] is False
    assert (await counter.get())["available"] is False
    assert len(calls) == 1
