"""No bank network or real funds: atomic live settlement against fixture orders."""

import json
import sqlite3
import time
from dataclasses import replace
from unittest.mock import patch

import pytest
from test_payments import setup

from nanomuse_cloud.live_billing import credit_units
from nanomuse_cloud.service import CloudError


async def fixture(monkeypatch, plan="", account=None, amount=59000, order="live_fixture"):
    _, client, user, p, _, _ = await setup(monkeypatch)
    account = account or user["account"]["id"]
    terms = p.plans()[plan] if plan else {}
    with p.db.tx() as c:
        c.execute(
            "INSERT INTO tbank_orders(id,account_id,idempotency,plan,amount,payment_id,created_at,terminal,plan_snapshot) VALUES(?,?,?,?,?,'123456',?,'fixtureLIVE',?)",
            (order, account, order, plan, amount, int(time.time()), json.dumps(terms)),
        )
    return client, p, account, p.order(order)


def event(row, status="CONFIRMED", amount=None):
    return {"Status": status, "Success": True, "PaymentId": row["payment_id"], "Amount": row["amount"] if amount is None else amount}


async def test_capture_atomic_and_replay(monkeypatch):
    client, p, account, row = await fixture(monkeypatch, plan="lika-plus", amount=149000)
    before = p.db.account(account)["grant_uy"]
    for _ in range(3):
        p.live_billing.apply(row, event(row), reconciled=True)
    assert p.db.account(account)["grant_uy"] == before + credit_units(p.cloud.s, 149000)
    assert p.db.subscription(account)["connect"] == 1
    with p.db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM tbank_live_events").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM payment_notice_outbox").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM tbank_demo_events").fetchone()[0] == 0
    await client.aclose()


async def test_topup_balance_threshold_activates_the_matching_tier(monkeypatch):
    client, p, account, row = await fixture(monkeypatch, amount=390000)
    p.live_billing.apply(row, event(row), reconciled=True)
    plan = p.db.subscription(account)
    assert plan is not None and plan["plan"] == "team" and plan["discount_pct"] == 30
    assert plan["expires_at"] - plan["started_at"] == 30 * 86400
    await client.aclose()


async def test_paid_balance_threshold_accumulates_after_spending(monkeypatch):
    client, p, account, first = await fixture(monkeypatch, amount=30000, order="threshold_first")
    p.live_billing.apply(first, event(first), reconciled=True)
    assert p.db.subscription(account) is None
    # Spend ₽100 from the purchased pool; bonuses and signup credits do not count toward a tier.
    spent = p.cloud.s.rub_to_uy(100)
    p.db.charge(account, "chat", "test-model", 1, 1, 1, "threshold_spend", cost_uy=spent, sale_uy=spent)
    with p.db.tx() as c:
        c.execute(
            "INSERT INTO tbank_orders(id,account_id,idempotency,plan,amount,payment_id,created_at,terminal,plan_snapshot) VALUES(?,?,?,'',40000,'123457',?,'fixtureLIVE','{}')",
            ("threshold_second", account, "threshold_second", int(time.time())),
        )
    second = p.order("threshold_second")
    p.live_billing.apply(second, event(second), reconciled=True)
    balance = p.db.account(account)["paid_balance_uy"]
    assert balance >= p.cloud.s.rub_to_uy(590)
    plan = p.db.subscription(account)
    assert plan is not None and plan["plan"] == "lika" and plan["discount_pct"] == 10
    await client.aclose()


async def test_capture_rollback_includes_money_terms_notice(monkeypatch):
    client, p, account, row = await fixture(monkeypatch, plan="lika")
    before = p.db.account(account)["grant_uy"]
    with patch("nanomuse_cloud.live_billing.PaymentOutbox.enqueue_settled", side_effect=sqlite3.OperationalError("fixture")):
        with pytest.raises(sqlite3.OperationalError):
            p.live_billing.apply(row, event(row), reconciled=True)
    assert p.db.account(account)["grant_uy"] == before
    assert p.db.subscription(account) is None and p.order(row["id"])["credited"] == 0
    with p.db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM tbank_live_events").fetchone()[0] == 0
    await client.aclose()


async def test_full_refund_uses_original_rate_and_revokes_terms(monkeypatch):
    client, p, account, row = await fixture(monkeypatch, plan="lika")
    before = p.db.account(account)["grant_uy"]
    p.live_billing.apply(row, event(row), reconciled=True)
    p.cloud.s = replace(p.cloud.s, rub_per_usd=150)
    for _ in range(3):
        p.live_billing.apply(p.order(row["id"]), event(row, "REFUNDED", 0), reconciled=True)
    p.live_billing.apply(p.order(row["id"]), event(row), reconciled=True)
    assert p.db.account(account)["grant_uy"] == before
    assert p.db.subscription(account) is None
    with p.db.tx() as c:
        assert c.execute("SELECT SUM(grant_delta_uy) FROM tbank_live_events").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM payment_notice_outbox").fetchone()[0] == 2
    await client.aclose()


async def test_partial_then_full_returns_rounding_remainder(monkeypatch):
    client, p, account, row = await fixture(monkeypatch, amount=20001)
    before = p.db.account(account)["grant_uy"]
    p.live_billing.apply(row, event(row), reconciled=True)
    p.live_billing.apply(p.order(row["id"]), event(row, "PARTIAL_REFUNDED", 10000), reconciled=True)
    snapshot = p.db.account(account)["grant_uy"]
    p.live_billing.apply(p.order(row["id"]), event(row, "PARTIAL_REFUNDED", 10000), reconciled=True)
    assert p.db.account(account)["grant_uy"] == snapshot
    p.live_billing.apply(p.order(row["id"]), event(row, "REFUNDED", 0), reconciled=True)
    assert p.db.account(account)["grant_uy"] == before
    await client.aclose()


async def test_missed_confirmation_full_refund_net_zero(monkeypatch):
    client, p, account, row = await fixture(monkeypatch, plan="lika")
    before = p.db.account(account)["grant_uy"]
    p.live_billing.apply(row, event(row, "REFUNDED", 0), reconciled=True)
    assert p.db.account(account)["grant_uy"] == before and p.db.subscription(account) is None
    await client.aclose()


async def test_hold_does_not_credit_and_demo_is_forbidden(monkeypatch):
    client, p, account, row = await fixture(monkeypatch)
    before = p.db.account(account)["grant_uy"]
    p.live_billing.apply(row, event(row, "AUTHORIZED"), reconciled=False)
    assert p.db.account(account)["grant_uy"] == before
    row["terminal"] = "fixtureDEMO"
    with pytest.raises(CloudError):
        p.live_billing.apply(row, event(row), reconciled=True)
    await client.aclose()


async def test_live_public_collection_stays_blocked(monkeypatch):
    client, p, account, row = await fixture(monkeypatch)
    p.terminal = "fixtureLIVE"
    with pytest.raises(CloudError):
        p.ready()
    await client.aclose()
