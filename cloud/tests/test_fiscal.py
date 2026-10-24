"""Receipt preparation is explicit and cannot enable production charging."""

from dataclasses import replace

import pytest
from test_accounts import auth
from test_payments import setup

from nanomuse_cloud.fiscal import FiscalConfig, ReceiptError, ReceiptPolicy


def fixture_config(ffd="1.05"):
    # Fixture choices illustrate the serializer, not the merchant's tax policy.
    return FiscalConfig(
        ffd_version=ffd,
        taxation="usn_income",
        measurement_unit="шт" if ffd == "1.2" else "",
        topup=ReceiptPolicy("Fixture credit", "advance", "payment", "none"),
        plan=ReceiptPolicy("Fixture digital service", "full_prepayment", "service", "vat22"),
    )


def test_missing_policy_is_reported_without_guessing_tax_or_echoing_values():
    config = FiscalConfig.from_env({})
    assert "TBANK_RECEIPT_TAXATION" in config.errors()
    assert "TBANK_RECEIPT_TOPUP_METHOD" in config.errors()
    assert config.diagnostics()["payload_configuration_valid"] is False
    with pytest.raises(ReceiptError, match="receipt_configuration"):
        config.prepare(kind="topup", amount_kopecks=20000, customer_email="person@example.test")


@pytest.mark.parametrize("ffd", ["1.05", "1.2"])
@pytest.mark.parametrize("amount", [1, 19999, 59000, 149000, 390000, 10000000])
def test_exact_kopecks_and_recipient_match_bank_schema(ffd, amount):
    receipt = fixture_config(ffd).prepare(kind="topup", amount_kopecks=amount, customer_email="person@example.test")
    assert receipt["Email"] == "person@example.test"
    item = receipt["Items"][0]
    assert item["Price"] * item["Quantity"] == item["Amount"] == amount
    assert item["PaymentMethod"] == "advance" and item["PaymentObject"] == "payment"
    if ffd == "1.2":
        assert receipt["FfdVersion"] == "1.2" and item["MeasurementUnit"] == "шт"
    else:
        assert "FfdVersion" not in receipt and "MeasurementUnit" not in item


def test_separate_reviewed_plan_policy_and_current_vat_enum():
    config = fixture_config()
    item = config.prepare(kind="plan", amount_kopecks=59000, customer_email="person@example.test")["Items"][0]
    assert item["PaymentMethod"] == "full_prepayment" and item["Tax"] == "vat22"
    assert replace(config, plan=replace(config.plan, tax="vat20")).errors() == ["TBANK_RECEIPT_PLAN_TAX"]
    assert replace(config, topup=replace(config.topup, payment_object="commodity")).errors()


@pytest.mark.parametrize("amount", [True, False, 0, -1, 10000001, 1.5, "20000"])
def test_invalid_amount_fails_before_bank_call(amount):
    with pytest.raises(ReceiptError, match="^receipt_amount$"):
        fixture_config().prepare(kind="topup", amount_kopecks=amount, customer_email="person@example.test")


@pytest.mark.parametrize(
    "email",
    ["", "person", "a@b", "a b@example.test", "a\n@example.test", "a@b.test\r\nBcc:x@y.test", "я@example.test", "a" * 60 + "@x.test", None],
)
def test_invalid_recipient_is_not_echoed_or_silently_truncated(email):
    with pytest.raises(ReceiptError, match="^receipt_email$"):
        fixture_config().prepare(kind="topup", amount_kopecks=20000, customer_email=email)


def test_supported_format_and_unit_must_be_explicit():
    config = fixture_config("1.2")
    assert replace(config, measurement_unit="").errors() == ["TBANK_RECEIPT_UNIT"]
    assert replace(config, ffd_version="1.1").errors() == ["TBANK_RECEIPT_FFD"]
    with pytest.raises(ReceiptError, match="^receipt_kind$"):
        config.prepare(kind="refund", amount_kopecks=20000, customer_email="person@example.test")


async def test_valid_fiscal_parameters_cannot_unlock_real_collection(monkeypatch):
    for key, value in {
        "FFD": "1.05",
        "TAXATION": "usn_income",
        "TOPUP_NAME": "Fixture credit",
        "TOPUP_METHOD": "advance",
        "TOPUP_OBJECT": "payment",
        "TOPUP_TAX": "none",
        "PLAN_NAME": "Fixture service",
        "PLAN_METHOD": "full_prepayment",
        "PLAN_OBJECT": "service",
        "PLAN_TAX": "none",
    }.items():
        monkeypatch.setenv("TBANK_RECEIPT_" + key, value)
    _, client, user, p, _, _ = await setup(monkeypatch)
    p.terminal = "fixture-live-terminal"
    ready = (await client.get("/v1/admin/payments/readiness", headers={"X-Admin-Token": "admin"})).json()
    assert ready["fiscal_preparation"]["payload_configuration_valid"]
    assert not ready["fiscal_preparation"]["receipt_delivery_verified"]
    assert not ready["ready_for_first_live_payment"] and not ready["live_enabled"]
    response = await client.post(
        "/v1/payments/orders",
        headers=auth(user["api_key"]),
        json={"amount": "200", "accept_terms": True, "idempotency_key": "fiscal_live_blocked_fixture"},
    )
    assert response.status_code == 503
    with p.db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM tbank_orders").fetchone()[0] == 0
    await client.aclose()
