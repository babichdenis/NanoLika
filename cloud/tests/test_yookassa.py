"""YooKassa adapter: authenticated status lookup, safe redirects and isolated tests."""

import json

import httpx
from test_accounts import auth, make
from test_cloud import sign_up


async def setup(monkeypatch, *, secret="test_fixture_key"):
    monkeypatch.setenv("PAYMENT_PROVIDER", "yookassa")
    monkeypatch.setenv("YOOKASSA_SHOP_ID", "1498091")
    monkeypatch.setenv("YOOKASSA_SECRET_KEY", secret)
    monkeypatch.delenv("YOOKASSA_LIVE_ENABLED", raising=False)
    monkeypatch.delenv("YOOKASSA_RECEIPTS_READY", raising=False)
    app, client, sender, _, cloud, _ = make(public_base="https://cloud.example.test")
    user = await sign_up(client, sender, "payer@example.test")
    payment = {}
    calls = []

    def provider(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path == "/v3/payments":
            body = json.loads(request.content)
            assert request.headers["Idempotence-Key"] == body["metadata"]["order_id"]
            payment.update(
                id="yk-fixture-0001",
                status="pending",
                paid=False,
                amount=body["amount"],
                confirmation={"confirmation_url": "https://yookassa.ru/checkout/fixture"},
                metadata=body["metadata"],
                recipient={"account_id": "1498091"},
            )
            return httpx.Response(201, json=payment)
        if request.method == "GET" and request.url.path == "/v3/payments/yk-fixture-0001":
            return httpx.Response(200, json=payment)
        if request.method == "GET" and request.url.path == "/v3/refunds":
            return httpx.Response(200, json={"items": [], "has_next": False})
        return httpx.Response(404, json={"type": "error"})

    app.state.payments.transport = httpx.MockTransport(provider)
    return app, client, user, app.state.payments, payment, calls


async def test_redirect_payment_is_idempotent_and_credits_only_after_server_confirmation(monkeypatch):
    _, client, user, payments, provider_payment, calls = await setup(monkeypatch)
    headers = auth(user["api_key"])
    payload = {"plan": "lika", "accept_terms": True, "idempotency_key": "yk_test_idempotency_001"}
    created = await client.post("/v1/payments/orders", headers=headers, json=payload)
    assert created.status_code == 200, created.text
    row = created.json()
    assert row["test_mode"] is True and row["provider"] == "yookassa"
    assert row["payment_url"] == "https://yookassa.ru/checkout/fixture"
    assert row["amount_kopecks"] == 59000
    again = await client.post("/v1/payments/orders", headers=headers, json=payload)
    assert again.json()["order_id"] == row["order_id"]
    assert len([call for call in calls if call[0] == "POST"]) == 1
    assert payments.wallet(payments.cloud.authenticate(user["api_key"]))["balance_kopecks"] == 0

    provider_payment["status"] = "succeeded"
    provider_payment["paid"] = True
    event = {"type": "notification", "event": "payment.succeeded", "object": {"id": provider_payment["id"]}}
    notified = await client.post("/v1/payments/yookassa/notify", json=event)
    assert notified.status_code == 200, notified.text
    assert payments.wallet(payments.cloud.authenticate(user["api_key"]))["balance_kopecks"] == 59000
    settled = await client.get("/v1/payments/orders/" + row["order_id"], headers=headers)
    assert settled.json()["credited"] is True
    await client.aclose()


async def test_live_key_stays_blocked_until_receipt_and_live_switch_are_set(monkeypatch):
    _, client, user, payments, _, _ = await setup(monkeypatch, secret="live_fixture_key")
    config = await client.get("/v1/payments/config")
    assert config.status_code == 503
    assert config.json()["error"]["code"] == "payment_live_blocked"
    created = await client.post(
        "/v1/payments/orders",
        headers=auth(user["api_key"]),
        json={"amount": "100", "accept_terms": True, "idempotency_key": "yk_live_disabled_001"},
    )
    assert created.status_code == 503
    with payments.db.tx() as db:
        assert db.execute("SELECT COUNT(*) FROM tbank_orders").fetchone()[0] == 0
    await client.aclose()


async def test_redirect_url_must_belong_to_yookassa(monkeypatch):
    _, client, user, payments, payment, _ = await setup(monkeypatch)
    headers = auth(user["api_key"])

    async def malicious(request):
        body = json.loads(request.content)
        payment.update(
            id="yk-fixture-evil",
            status="pending",
            amount=body["amount"],
            confirmation={"confirmation_url": "https://attacker.example/"},
            metadata=body["metadata"],
            recipient={"account_id": "1498091"},
        )
        return httpx.Response(201, json=payment)

    payments.transport = httpx.MockTransport(malicious)
    response = await client.post(
        "/v1/payments/orders",
        headers=headers,
        json={"amount": "100", "accept_terms": True, "idempotency_key": "yk_bad_redirect_001"},
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "payment_url"
    await client.aclose()


async def test_reconciliation_can_recover_yookassa_payment_id(monkeypatch):
    _, client, user, payments, provider_payment, calls = await setup(monkeypatch)
    created = await client.post(
        "/v1/payments/orders",
        headers=auth(user["api_key"]),
        json={"amount": "100", "accept_terms": True, "idempotency_key": "yk_recover_order_001"},
    )
    assert created.status_code == 200, created.text
    row = payments.order(created.json()["order_id"])
    row["payment_id"] = None
    recovered = await payments.bank("CheckOrder", {"OrderId": row["id"]})
    assert recovered["OrderId"] == row["id"]
    assert recovered["Payments"] == [{"PaymentId": provider_payment["id"], "Amount": 10000}]
    assert len([call for call in calls if call[0] == "POST"]) == 2  # same idempotency key returns same payment
    await client.aclose()


async def test_untrusted_notification_id_cannot_credit_balance(monkeypatch):
    _, client, user, payments, _, _ = await setup(monkeypatch)
    created = await client.post(
        "/v1/payments/orders",
        headers=auth(user["api_key"]),
        json={"amount": "100", "accept_terms": True, "idempotency_key": "yk_untrusted_event_001"},
    )
    assert created.status_code == 200, created.text
    response = await client.post(
        "/v1/payments/yookassa/notify",
        json={"type": "notification", "event": "payment.succeeded", "object": {"id": "attacker-payment"}},
    )
    assert response.status_code == 502
    assert payments.wallet(payments.cloud.authenticate(user["api_key"]))["balance_kopecks"] == 0
    await client.aclose()
