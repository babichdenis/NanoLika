"""Robokassa callbacks are signed, account-scoped and isolated in test mode."""

from urllib.parse import parse_qs, quote, urlsplit

import httpx
import pytest
from test_accounts import auth, make
from test_cloud import sign_up

from nanomuse_cloud.robokassa import RobokassaPayments


async def setup(monkeypatch):
    monkeypatch.setenv("PAYMENT_PROVIDER", "robokassa")
    monkeypatch.setenv("ROBOKASSA_MERCHANT_LOGIN", "nanolika")
    monkeypatch.setenv("ROBOKASSA_TEST_MODE", "1")
    monkeypatch.setenv("ROBOKASSA_TEST_PASSWORD_1", "fixture-test-password-one")
    monkeypatch.setenv("ROBOKASSA_TEST_PASSWORD_2", "fixture-test-password-two")
    monkeypatch.delenv("ROBOKASSA_LIVE_ENABLED", raising=False)
    monkeypatch.delenv("ROBOKASSA_RECEIPTS_READY", raising=False)
    app, client, sender, _, cloud, _ = make(public_base="https://cloud.example.test")
    user = await sign_up(client, sender, "payer@example.test")
    return app, client, user, app.state.payments


def callback_fields(payments: RobokassaPayments, order: dict) -> dict[str, str]:
    invoice_id = str(order["payment_id"])
    out_sum = f"{order['amount'] / 100:.2f}"
    custom = {"Shp_order": order["id"]}
    signature = payments._signature(
        ":".join([out_sum, invoice_id, payments.password2, *(f"{key}={value}" for key, value in sorted(custom.items()))])
    )
    return {"OutSum": out_sum, "InvId": invoice_id, "SignatureValue": signature, **custom}


@pytest.mark.asyncio
async def test_hosted_checkout_and_signed_result_credit_once_in_test_wallet(monkeypatch):
    _, client, user, payments = await setup(monkeypatch)
    payload = {"plan": "lika", "accept_terms": True, "idempotency_key": "robokassa_test_order_001"}
    created = await client.post("/v1/payments/orders", headers=auth(user["api_key"]), json=payload)
    assert created.status_code == 200, created.text
    result = created.json()
    assert result["test_mode"] is True
    assert result["amount_kopecks"] == 59000
    parts = urlsplit(result["payment_url"])
    assert parts.scheme == "https" and parts.hostname == "auth.robokassa.ru"
    query = {key: values[0] for key, values in parse_qs(parts.query).items()}
    expected = payments._signature(
        ":".join([query["MerchantLogin"], query["OutSum"], query["InvId"], payments.password1, "Shp_order=" + result["order_id"]])
    )
    assert query["SignatureValue"] == expected
    assert query["IsTest"] == "1"
    return_url = "https://cloud.example.test/payments/checkout?order=" + result["order_id"]
    assert query["SuccessUrl2"] == return_url
    assert query["SuccessUrl2Method"] == "GET"
    assert query["FailUrl2"] == return_url
    assert query["FailUrl2Method"] == "GET"

    row = payments.order(result["order_id"])
    fields = callback_fields(payments, row)
    first = await client.post("/v1/payments/robokassa/notify", data=fields)
    again = await client.post("/v1/payments/robokassa/notify", data=fields)
    assert first.status_code == again.status_code == 200
    assert first.text == "OK" + str(row["payment_id"])
    wallet = payments.wallet(payments.cloud.authenticate(user["api_key"]))
    assert wallet["balance_kopecks"] == 59000
    assert wallet["test_mode"] is True
    await client.aclose()


@pytest.mark.asyncio
async def test_invalid_signature_and_amount_do_not_credit(monkeypatch):
    _, client, user, payments = await setup(monkeypatch)
    created = await client.post(
        "/v1/payments/orders",
        headers=auth(user["api_key"]),
        json={"amount": "100", "accept_terms": True, "idempotency_key": "robokassa_bad_result_001"},
    )
    assert created.status_code == 200, created.text
    row = payments.order(created.json()["order_id"])
    fields = callback_fields(payments, row)
    response = await client.post("/v1/payments/robokassa/notify", data={**fields, "SignatureValue": "0" * 32})
    assert response.status_code == 403
    mismatched = {**fields, "OutSum": "99.00"}
    mismatched["SignatureValue"] = payments._signature(
        ":".join([mismatched["OutSum"], mismatched["InvId"], payments.password2, "Shp_order=" + mismatched["Shp_order"]])
    )
    response = await client.post("/v1/payments/robokassa/notify", data=mismatched)
    assert response.status_code == 409
    assert payments.wallet(payments.cloud.authenticate(user["api_key"]))["balance_kopecks"] == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_live_checkout_stays_closed_until_receipt_and_operator_flags(monkeypatch):
    monkeypatch.setenv("PAYMENT_PROVIDER", "robokassa")
    monkeypatch.setenv("ROBOKASSA_MERCHANT_LOGIN", "nanolika")
    monkeypatch.setenv("ROBOKASSA_PASSWORD_1", "fixture-live-password-one")
    monkeypatch.setenv("ROBOKASSA_PASSWORD_2", "fixture-live-password-two")
    monkeypatch.setenv("ROBOKASSA_TEST_MODE", "0")
    monkeypatch.setenv("ROBOKASSA_LIVE_ENABLED", "1")
    monkeypatch.setenv("ROBOKASSA_RECEIPTS_READY", "1")
    monkeypatch.delenv("ROBOKASSA_RECEIPT_TAXATION", raising=False)
    app, client, sender, _, _, _ = make(public_base="https://cloud.example.test")
    await sign_up(client, sender, "payer@example.test")
    config = await client.get("/v1/payments/config")
    assert config.status_code == 503
    assert config.json()["error"]["code"] == "payment_receipt"
    with app.state.payments.db.tx() as db:
        assert db.execute("SELECT COUNT(*) FROM tbank_orders").fetchone()[0] == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_live_form_signs_receipt_and_state_lookup_is_server_authenticated(monkeypatch):
    monkeypatch.setenv("PAYMENT_PROVIDER", "robokassa")
    monkeypatch.setenv("ROBOKASSA_MERCHANT_LOGIN", "nanolika")
    monkeypatch.setenv("ROBOKASSA_PASSWORD_1", "fixture-live-password-one")
    monkeypatch.setenv("ROBOKASSA_PASSWORD_2", "fixture-live-password-two")
    monkeypatch.setenv("ROBOKASSA_TEST_MODE", "0")
    monkeypatch.setenv("ROBOKASSA_LIVE_ENABLED", "1")
    monkeypatch.setenv("ROBOKASSA_RECEIPTS_READY", "1")
    monkeypatch.setenv("ROBOKASSA_RECEIPT_NAME", "Пополнение баланса nanoLika Cloud")
    monkeypatch.setenv("ROBOKASSA_RECEIPT_TAX", "none")
    monkeypatch.setenv("ROBOKASSA_RECEIPT_TAXATION", "usn_income")
    monkeypatch.setenv("ROBOKASSA_RECEIPT_METHOD", "advance")
    monkeypatch.setenv("ROBOKASSA_RECEIPT_OBJECT", "payment")
    app, client, sender, _, _, _ = make(public_base="https://cloud.example.test")
    user = await sign_up(client, sender, "payer@example.test")
    payments: RobokassaPayments = app.state.payments
    requests = []

    def provider(request):
        requests.append(request)
        assert request.url.path.endswith("/OpStateExt")
        assert request.url.params["Signature"] == payments._signature(
            f"nanolika:{request.url.params['InvoiceID']}:fixture-live-password-two"
        )
        return httpx.Response(
            200,
            content=b'<OperationStateResponse xmlns="urn:robokassa"><Result><Code>0</Code></Result><State><Code>100</Code></State></OperationStateResponse>',
        )

    payments.transport = httpx.MockTransport(provider)
    created = await client.post(
        "/v1/payments/orders",
        headers=auth(user["api_key"]),
        json={"amount": "100", "accept_terms": True, "idempotency_key": "robokassa_live_fixture_001"},
    )
    assert created.status_code == 200, created.text
    order = created.json()
    params = {key: values[0] for key, values in parse_qs(urlsplit(order["payment_url"]).query).items()}
    receipt = params["Receipt"]
    expected = payments._signature(
        ":".join(
            [
                "nanolika",
                "100.00",
                params["InvId"],
                quote(receipt, safe=""),
                "fixture-live-password-one",
                "Shp_order=" + order["order_id"],
            ]
        )
    )
    assert params["SignatureValue"] == expected
    state = await payments.bank("GetState", {"PaymentId": params["InvId"]})
    assert state["Status"] == "CONFIRMED" and state["Amount"] == 10000
    assert len(requests) == 1
    await client.aclose()
