"""Refunds use bank current remaining amounts, never browser or webhook arithmetic."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_accounts import auth
from test_payments import notice, setup

from nanomuse_cloud.payments import Payments
from nanomuse_cloud.service import CloudError


async def order(client, user, label, *, plan="", amount="200"):
    body = {"accept_terms": True, "idempotency_key": "refund_fixture_" + label}
    body.update({"plan": plan} if plan else {"amount": amount})
    result = await client.post("/v1/payments/orders", headers=auth(user["api_key"]), json=body)
    assert result.status_code == 200, result.text
    return result.json()


def bank_state(p, bank, row, status, remaining):
    pid = p.order(row["order_id"])["payment_id"]
    bank[pid].update(Status=status, Amount=remaining, Success=True)
    return dict(bank[pid])


async def test_full_refund_is_exactly_once_and_cannot_be_recredited(monkeypatch):
    app, client, user, p, calls, bank = await setup(monkeypatch)
    row = await order(client, user, "full")
    p.apply(notice(p, row), signed=True)
    bank_state(p, bank, row, "REFUNDED", 0)
    for _ in range(3):
        response = await client.post("/v1/payments/tbank/notify", json=notice(p, row, Status="REFUNDED", Amount=0))
        assert response.status_code == 200, response.text
    assert p.wallet(p.cloud.authenticate(user["api_key"]))["balance_kopecks"] == 0
    for late in ("CONFIRMED", "AUTHORIZED", "NEW"):
        p.apply(notice(p, row, Status=late), signed=True)
    view = p.view(p.order(row["order_id"]))
    assert view["status"] == "REFUNDED" and not view["credited"] and view["was_credited"]
    assert view["refunded_amount_kopecks"] == 20000 and view["net_credited_amount_kopecks"] == 0
    with p.db.tx() as c:
        events = c.execute("SELECT status,amount_delta FROM tbank_demo_events WHERE order_id=? ORDER BY id", (row["order_id"],)).fetchall()
    assert [(r["status"], r["amount_delta"]) for r in events] == [("CONFIRMED", 20000), ("REFUNDED", -20000)]
    assert p.wallet(p.cloud.authenticate(user["api_key"]))["balance_kopecks"] == 0
    await client.aclose()


async def test_missing_refund_webhook_is_reconciled_even_after_credit(monkeypatch):
    _, client, user, p, _, bank = await setup(monkeypatch)
    row = await order(client, user, "reconcile")
    p.apply(notice(p, row), signed=True)
    bank_state(p, bank, row, "REFUNDED", 0)
    r = await client.get("/v1/payments/orders/" + row["order_id"], headers=auth(user["api_key"]))
    assert r.status_code == 200 and r.json()["status"] == "REFUNDED" and not r.json()["credited"]
    assert p.wallet(p.cloud.authenticate(user["api_key"]))["balance_kopecks"] == 0
    await client.aclose()


async def test_partial_refunds_use_cumulative_getstate_not_replayed_notice(monkeypatch):
    _, client, user, p, _, bank = await setup(monkeypatch)
    row = await order(client, user, "partial")
    p.apply(notice(p, row), signed=True)
    bank_state(p, bank, row, "PARTIAL_REFUNDED", 15000)
    # The notice Amount is one operation, not a cumulative remaining balance.
    payload = notice(p, row, Status="PARTIAL_REFUNDED", Amount=5000)
    for _ in range(2):
        r = await client.post("/v1/payments/tbank/notify", json=payload)
        assert r.status_code == 200, r.text
    caller = p.cloud.authenticate(user["api_key"])
    assert p.wallet(caller)["balance_kopecks"] == 15000
    bank_state(p, bank, row, "PARTIAL_REFUNDED", 12000)
    assert (await client.post("/v1/payments/tbank/notify", json=notice(p, row, Status="PARTIAL_REFUNDED", Amount=3000))).status_code == 200
    assert p.wallet(caller)["balance_kopecks"] == 12000
    # An earlier already-in-flight GetState response must never restore refunded money.
    p.apply(bank_state(p, bank, row, "PARTIAL_REFUNDED", 15000), reconciled=True)
    assert p.wallet(caller)["balance_kopecks"] == 12000
    bank_state(p, bank, row, "REFUNDED", 0)
    assert (await client.post("/v1/payments/tbank/notify", json=notice(p, row, Status="REFUNDED", Amount=0))).status_code == 200
    assert p.wallet(caller)["balance_kopecks"] == 0
    await client.aclose()


async def test_refund_before_confirmation_never_creates_phantom_balance(monkeypatch):
    _, client, user, p, _, bank = await setup(monkeypatch)
    row = await order(client, user, "before")
    state = bank_state(p, bank, row, "REFUNDED", 0)
    p.apply(state, reconciled=True)
    p.apply(notice(p, row), signed=True)
    assert p.wallet(p.cloud.authenticate(user["api_key"]))["balance_kopecks"] == 0
    assert not p.view(p.order(row["order_id"]))["credited"]
    await client.aclose()


async def test_partial_refund_can_repair_missing_confirmation(monkeypatch):
    _, client, user, p, _, bank = await setup(monkeypatch)
    row = await order(client, user, "partial_before")
    p.apply(bank_state(p, bank, row, "PARTIAL_REFUNDED", 15000), reconciled=True)
    p.apply(notice(p, row), signed=True)
    assert p.wallet(p.cloud.authenticate(user["api_key"]))["balance_kopecks"] == 15000
    await client.aclose()


async def test_refunding_package_restores_only_previously_funded_rights(monkeypatch):
    import nanomuse_cloud.payments as module

    _, client, user, p, _, bank = await setup(monkeypatch)
    monkeypatch.setattr(module.time, "time", lambda: 100000)
    earlier = await order(client, user, "earlier", plan="lika-plus")
    p.apply(notice(p, earlier), signed=True)
    caller = p.cloud.authenticate(user["api_key"])
    original_expiry = p.wallet(caller)["expires_at"]
    monkeypatch.setattr(module.time, "time", lambda: 200000)
    upgraded = await order(client, user, "upgraded", plan="team")
    p.apply(notice(p, upgraded), signed=True)
    topup = await order(client, user, "topup", amount="300")
    p.apply(notice(p, topup), signed=True)
    p.apply(bank_state(p, bank, upgraded, "REFUNDED", 0), reconciled=True)
    wallet = p.wallet(caller)
    assert wallet["plan"] == "lika-plus" and wallet["expires_at"] == original_expiry
    assert wallet["balance_kopecks"] == 149000 + 30000
    p.apply(bank_state(p, bank, earlier, "REFUNDED", 0), reconciled=True)
    wallet = p.wallet(caller)
    assert wallet["plan"] == "" and wallet["expires_at"] == 0 and wallet["balance_kopecks"] == 30000
    await client.aclose()


async def test_refund_retains_debt_and_does_not_touch_other_account_or_real_grant(monkeypatch):
    _, client, user, p, _, bank = await setup(monkeypatch)
    row = await order(client, user, "debt")
    p.apply(notice(p, row), signed=True)
    account = user["account"]["id"]
    real_before = dict(p.db.account(account))["grant_uy"]
    with p.db.tx() as c:
        c.execute("UPDATE tbank_demo_wallets SET balance=1000 WHERE account_id=?", (account,))
    p.apply(bank_state(p, bank, row, "REFUNDED", 0), reconciled=True)
    assert p.wallet(p.cloud.authenticate(user["api_key"]))["balance_kopecks"] == -19000
    assert dict(p.db.account(account))["grant_uy"] == real_before
    await client.aclose()


async def test_refund_transaction_rolls_back_and_retry_is_safe(monkeypatch):
    _, client, user, p, _, bank = await setup(monkeypatch)
    row = await order(client, user, "rollback")
    p.apply(notice(p, row), signed=True)
    with p.db.tx() as c:
        c.execute(
            "CREATE TRIGGER fail_refund BEFORE INSERT ON tbank_demo_events WHEN NEW.status='REFUNDED' BEGIN SELECT RAISE(FAIL,'fixture'); END"
        )
    state = bank_state(p, bank, row, "REFUNDED", 0)
    with pytest.raises(sqlite3.IntegrityError):
        p.apply(state, reconciled=True)
    assert p.order(row["order_id"])["refunded"] == 0
    assert p.wallet(p.cloud.authenticate(user["api_key"]))["balance_kopecks"] == 20000
    with p.db.tx() as c:
        c.execute("DROP TRIGGER fail_refund")
    p.apply(state, reconciled=True)
    assert p.wallet(p.cloud.authenticate(user["api_key"]))["balance_kopecks"] == 0
    await client.aclose()


async def test_concurrent_duplicate_refunds_reverse_once(monkeypatch):
    _, client, user, p, _, bank = await setup(monkeypatch)
    row = await order(client, user, "threads")
    p.apply(notice(p, row), signed=True)
    state = bank_state(p, bank, row, "REFUNDED", 0)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: p.apply(state, reconciled=True), range(8)))
    assert p.wallet(p.cloud.authenticate(user["api_key"]))["balance_kopecks"] == 0
    await client.aclose()


async def test_invalid_refunds_are_rejected_before_bank_or_balance_change(monkeypatch):
    _, client, user, p, calls, bank = await setup(monkeypatch)
    row = await order(client, user, "invalid")
    p.apply(notice(p, row), signed=True)
    initial = len(calls)
    for overrides in ({"PaymentId": "9999"}, {"TerminalKey": "otherDEMO"}, {"Amount": -1}, {"Amount": 20001}):
        payload = notice(p, row, Status="REFUNDED", **overrides)
        r = await client.post("/v1/payments/tbank/notify", json=payload)
        assert r.status_code in (403, 409)
    bad = notice(p, row, Status="REFUNDED", Amount=0)
    bad["Token"] = "forged"
    assert (await client.post("/v1/payments/tbank/notify", json=bad)).status_code == 403
    assert len(calls) == initial
    with pytest.raises(CloudError):
        p.apply(notice(p, row, Status="REFUNDED", Amount=0), signed=True)
    with pytest.raises(CloudError):
        p.apply(bank_state(p, bank, row, "REFUNDED", 20000), reconciled=True)
    assert p.wallet(p.cloud.authenticate(user["api_key"]))["balance_kopecks"] == 20000
    await client.aclose()


async def test_legacy_order_and_wallet_migrate_without_reset(monkeypatch):
    _, client, user, p, _, bank = await setup(monkeypatch)
    row = await order(client, user, "legacy")
    p.apply(notice(p, row), signed=True)
    # Repeated initialization must not replay credits or reverse them before bank proof.
    again = Payments(p.cloud, p.terminal, p.password, p.public_base, transport=p.transport)
    assert again.wallet(p.cloud.authenticate(user["api_key"]))["balance_kopecks"] == 20000
    p.apply(bank_state(p, bank, row, "REFUNDED", 0), reconciled=True)
    again = Payments(p.cloud, p.terminal, p.password, p.public_base, transport=p.transport)
    assert again.wallet(p.cloud.authenticate(user["api_key"]))["balance_kopecks"] == 0
    await client.aclose()


async def test_a_refund_takes_the_tariff_back_down(monkeypatch):
    """Возврат пересчитывает уровень: сумма упала ниже тарифа — права снимаются."""
    app, client, user, p, calls, bank = await setup(monkeypatch)
    row = await order(client, user, "tier_down", plan="lika-plus")
    p.apply(notice(p, row), signed=True)
    # DEMO money and its tariff live in the demo wallet; the live subscription is not touched
    caller = p.cloud.authenticate(user["api_key"])
    assert p.wallet(caller)["plan"] == "lika-plus"
    # возвращаем всё: денег на счету нет, тариф снят
    bank_state(p, bank, row, "REFUNDED", 0)
    for _ in range(2):
        response = await client.post("/v1/payments/tbank/notify", json=notice(p, row, Status="REFUNDED", Amount=0))
        assert response.status_code == 200, response.text
    assert p.wallet(caller)["balance_kopecks"] == 0
    assert p.wallet(caller)["plan"] == ""
    await client.aclose()
