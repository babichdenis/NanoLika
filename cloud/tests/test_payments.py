"""Money boundary tests: signed confirmations, exact amounts and isolated DEMO credit."""

import hashlib
import json
import sqlite3

import httpx
import pytest
from test_accounts import auth, make
from test_cloud import sign_up

from nanomuse_cloud.payments import PLANS, cents, token
from nanomuse_cloud.service import CloudError


async def setup(monkeypatch):
    monkeypatch.setenv("TBANK_TERMINAL_KEY", "fixtureDEMO")
    monkeypatch.setenv("TBANK_PASSWORD", "fixture-password")
    monkeypatch.setenv("TBANK_PUBLIC_BASE", "https://cloud.example.test")
    app, client, sender, _, cloud, _ = make()
    user = await sign_up(client, sender, "payer@example.test")
    p = app.state.payments
    calls = []
    bank_orders = {}

    def bank(request):
        body = json.loads(request.content)
        assert body["Token"] == token(body, p.password)
        calls.append(request.url.path)
        if request.url.path.endswith("Init"):
            payment_id = str(len(bank_orders) + 12345)
            data = dict(
                Success=True,
                TerminalKey=p.terminal,
                OrderId=body["OrderId"],
                Amount=body["Amount"],
                PaymentId=payment_id,
                Status="NEW",
                PaymentURL="https://pay.tbank-online.com/fixture",
            )
            bank_orders[payment_id] = data
            return httpx.Response(200, json=data)
        return httpx.Response(200, json=bank_orders[body["PaymentId"]])

    p.transport = httpx.MockTransport(bank)
    return app, client, user, p, calls, bank_orders


def notice(p, row, **overrides):
    data = dict(
        TerminalKey=p.terminal,
        OrderId=row["order_id"],
        PaymentId=p.order(row["order_id"])["payment_id"],
        Amount=row["amount_kopecks"],
        Success=True,
        Status="CONFIRMED",
    )
    data.update(overrides)
    data["Token"] = token(data, p.password)
    return data


def test_signature_rules():
    body = {"TerminalKey": "fixtureDEMO", "Amount": 123, "Success": True, "DATA": {"ignored": True}, "Nothing": None}
    assert token(body, "fixture") == hashlib.sha256(b"123fixturetruefixtureDEMO").hexdigest()
    assert token(dict(body, Token="ignored"), "fixture") == token(body, "fixture")


@pytest.mark.parametrize("amount", ["0", "-1", "0.001", "nan", "Infinity", "100000.01", "bad"])
def test_invalid_amount(amount):
    with pytest.raises(CloudError):
        cents(amount)


@pytest.mark.parametrize("amount,wanted", [("0.01", 1), ("200.25", 20025), ("100000", 10000000)])
def test_cents(amount, wanted):
    assert cents(amount) == wanted


async def test_confirmation_is_exactly_once_and_demo_only(monkeypatch):
    _, client, user, p, calls, _ = await setup(monkeypatch)
    account = user["account"]["id"]
    before = dict(p.db.account(account))
    request = {"plan": "team", "accept_terms": True, "idempotency_key": "idempotent_fixture_1"}
    response = await client.post("/v1/payments/orders", headers=auth(user["api_key"]), json=request)
    assert response.status_code == 200, response.text
    row = response.json()
    assert row["amount_kopecks"] == PLANS["team"]["amount"]
    again = await client.post("/v1/payments/orders", headers=auth(user["api_key"]), json=request)
    assert again.json()["order_id"] == row["order_id"] and len(calls) == 1
    # Hold is not a sale. Browser return cannot credit an account either.
    r = await client.post("/v1/payments/tbank/notify", json=notice(p, row, Status="AUTHORIZED"))
    assert r.text == "OK"
    assert p.wallet(p.cloud.authenticate(user["api_key"]))["balance_kopecks"] == 0
    r = await client.get("/payments/checkout?order=" + row["order_id"] + "&success=true")
    assert r.status_code == 200
    for _ in range(3):
        r = await client.post("/v1/payments/tbank/notify", json=notice(p, row))
        assert r.status_code == 200 and r.text == "OK"
    wallet = p.wallet(p.cloud.authenticate(user["api_key"]))
    assert wallet["balance_kopecks"] == 390000 and wallet["plan"] == "team"
    await client.post("/v1/payments/tbank/notify", json=notice(p, row, Status="AUTHORIZED"))
    assert p.order(row["order_id"])["status"] == "CONFIRMED"
    # Authentication legitimately advances last_seen_at, even when a test crosses a second.
    # Every other field, including the real financial state, must remain unchanged.
    after = dict(p.db.account(account))
    assert after.pop("last_seen_at") >= before.pop("last_seen_at")
    assert after == before  # real money untouched
    assert p.db.subscription(account) is None
    await client.aclose()


async def test_untrusted_events_and_foreign_account(monkeypatch):
    _, client, user, p, _, _ = await setup(monkeypatch)
    r = await client.post(
        "/v1/payments/orders",
        headers=auth(user["api_key"]),
        json={"amount": "200.25", "accept_terms": True, "idempotency_key": "idempotent_fixture_2"},
    )
    row = r.json()
    bad = notice(p, row)
    bad["Amount"] += 1
    assert (await client.post("/v1/payments/tbank/notify", json=bad)).status_code == 403
    assert (await client.post("/v1/payments/tbank/notify", json=notice(p, row, Amount=20000))).status_code == 409
    assert (await client.post("/v1/payments/tbank/notify", json=notice(p, row, PaymentId="999999"))).status_code == 409
    assert (await client.post("/v1/payments/tbank/notify", json=notice(p, row, TerminalKey="otherDEMO"))).status_code == 403
    assert (
        await client.post("/v1/payments/orders", json={"amount": "200", "accept_terms": True, "idempotency_key": "idempotent_fixture_3"})
    ).status_code == 401
    assert (await client.get("/v1/payments/orders/" + row["order_id"])).status_code == 401
    assert (
        await client.post(
            "/v1/payments/orders",
            headers=auth(user["api_key"]),
            json={"plan": "team", "amount": "1", "accept_terms": True, "idempotency_key": "idempotent_fixture_4"},
        )
    ).status_code == 400
    # All supported callers still get an ownership check in the store.
    with pytest.raises(CloudError) as exc:
        p.order(row["order_id"], "not-the-owner")
    assert exc.value.status == 404
    await client.aclose()


async def test_getstate_reconciles_missing_webhook_and_topup_does_not_extend(monkeypatch):
    _, client, user, p, _, bank = await setup(monkeypatch)
    headers = auth(user["api_key"])
    row = (
        await client.post(
            "/v1/payments/orders", headers=headers, json={"plan": "plus", "accept_terms": True, "idempotency_key": "idempotent_fixture_5"}
        )
    ).json()
    bank[p.order(row["order_id"])["payment_id"]]["Status"] = "CONFIRMED"
    result = await client.get("/v1/payments/orders/" + row["order_id"], headers=headers)
    assert result.json()["credited"] is True
    caller = p.cloud.authenticate(user["api_key"])
    expiry = p.wallet(caller)["expires_at"]
    row = (
        await client.post(
            "/v1/payments/orders", headers=headers, json={"amount": "200", "accept_terms": True, "idempotency_key": "idempotent_fixture_6"}
        )
    ).json()
    await client.post("/v1/payments/tbank/notify", json=notice(p, row))
    wallet = p.wallet(caller)
    assert wallet["balance_kopecks"] == 169000 and wallet["expires_at"] == expiry
    await client.aclose()


async def test_transaction_rollback_does_not_lose_credit(monkeypatch):
    _, client, user, p, _, _ = await setup(monkeypatch)
    row = (
        await client.post(
            "/v1/payments/orders",
            headers=auth(user["api_key"]),
            json={"plan": "lika", "accept_terms": True, "idempotency_key": "idempotent_fixture_7"},
        )
    ).json()
    with p.db.tx() as c:
        c.execute("CREATE TRIGGER fail_demo BEFORE INSERT ON tbank_demo_events BEGIN SELECT RAISE(FAIL,'fixture'); END")
    with pytest.raises(sqlite3.IntegrityError):
        p.apply(notice(p, row), signed=True)
    assert not p.order(row["order_id"])["credited"]
    with p.db.tx() as c:
        c.execute("DROP TRIGGER fail_demo")
    p.apply(notice(p, row), signed=True)
    assert p.wallet(p.cloud.authenticate(user["api_key"]))["balance_kopecks"] == 59000
    await client.aclose()


async def test_production_terminal_cannot_be_enabled_by_accident(monkeypatch):
    _, client, _, p, _, _ = await setup(monkeypatch)
    p.terminal = "production-terminal"
    assert (await client.get("/v1/payments/config")).status_code == 503
    await client.aclose()
