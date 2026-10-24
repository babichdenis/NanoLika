"""Validated T-Bank receipt preparation, without live collection or fiscal delivery.

Values must come from the merchant's accountant and cash-register settings. A valid
payload is not evidence that a register is connected or that a receipt was issued.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

TAXATIONS = {"osn", "usn_income", "usn_income_outcome", "esn", "patent"}
TAXES = {"none", "vat0", "vat5", "vat7", "vat10", "vat22", "vat105", "vat107", "vat110", "vat122"}
METHODS = {"full_prepayment", "prepayment", "advance", "full_payment", "partial_payment", "credit", "credit_payment"}
# Intentionally limited to non-marked digital products, not a generic goods/register SDK.
OBJECTS = {"service", "intellectual_activity", "payment", "another"}


class ReceiptError(ValueError):
    """Only field names and stable codes, never customer data or configuration values."""


@dataclass(frozen=True)
class ReceiptPolicy:
    name: str = ""
    payment_method: str = ""
    payment_object: str = ""
    tax: str = ""

    def errors(self, prefix: str) -> list[str]:
        errors = []
        if not self.name.strip() or len(self.name) > 128 or any(ord(c) < 32 for c in self.name):
            errors.append(prefix + "_NAME")
        for field, value, choices in (
            ("METHOD", self.payment_method, METHODS),
            ("OBJECT", self.payment_object, OBJECTS),
            ("TAX", self.tax, TAXES),
        ):
            if value not in choices:
                errors.append(prefix + "_" + field)
        return errors


@dataclass(frozen=True)
class FiscalConfig:
    ffd_version: str = ""
    taxation: str = ""
    measurement_unit: str = ""
    topup: ReceiptPolicy = ReceiptPolicy()
    plan: ReceiptPolicy = ReceiptPolicy()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> FiscalConfig:
        source = os.environ if env is None else env

        def get(key):
            return source.get("TBANK_RECEIPT_" + key, "").strip()

        def policy(kind):
            return ReceiptPolicy(get(kind + "_NAME"), get(kind + "_METHOD"), get(kind + "_OBJECT"), get(kind + "_TAX"))

        return cls(get("FFD"), get("TAXATION"), get("UNIT"), policy("TOPUP"), policy("PLAN"))

    def errors(self) -> list[str]:
        errors = []
        if self.ffd_version not in {"1.05", "1.2"}:
            errors.append("TBANK_RECEIPT_FFD")
        if self.taxation not in TAXATIONS:
            errors.append("TBANK_RECEIPT_TAXATION")
        # Only single-unit digital positions are supported by this preparer.
        if self.ffd_version == "1.2" and self.measurement_unit != "шт":
            errors.append("TBANK_RECEIPT_UNIT")
        return errors + self.topup.errors("TBANK_RECEIPT_TOPUP") + self.plan.errors("TBANK_RECEIPT_PLAN")

    def diagnostics(self) -> dict:
        errors = self.errors()
        return {
            "payload_configuration_valid": not errors,
            "invalid_or_missing_fields": errors,
            "payload_connected_to_checkout": False,
            "cash_register_verified": False,
            "receipt_delivery_verified": False,
            "closing_receipts_implemented": False,
        }

    def prepare(self, *, kind: str, amount_kopecks: int, customer_email: str) -> dict:
        """Prepare one reviewed digital line. Does not send, store, charge or issue a receipt.

        A combined wallet/package sale must be reviewed before using a single line.
        Refund and closing receipts need their own original-order item allocation.
        """
        errors = self.errors()
        if errors:
            raise ReceiptError("receipt_configuration:" + ",".join(errors))
        if kind not in {"topup", "plan"}:
            raise ReceiptError("receipt_kind")
        if type(amount_kopecks) is not int or not 1 <= amount_kopecks <= 10_000_000:
            raise ReceiptError("receipt_amount")
        # Fail closed rather than silently truncating or changing a receipt recipient.
        if (
            not isinstance(customer_email, str)
            or len(customer_email) > 64
            or not customer_email.isascii()
            or not re.fullmatch(r"[^\s@<>\x00-\x1f\x7f]+@[^\s@<>\x00-\x1f\x7f]+\.[^\s@<>\x00-\x1f\x7f]+", customer_email)
        ):
            raise ReceiptError("receipt_email")
        policy = self.topup if kind == "topup" else self.plan
        item = {
            "Name": policy.name,
            "Price": amount_kopecks,
            "Quantity": 1,
            "Amount": amount_kopecks,
            "Tax": policy.tax,
            "PaymentMethod": policy.payment_method,
            "PaymentObject": policy.payment_object,
        }
        receipt = {"Email": customer_email, "Taxation": self.taxation, "Items": [item]}
        if self.ffd_version == "1.2":
            receipt["FfdVersion"] = "1.2"
            item["MeasurementUnit"] = self.measurement_unit
        # For FFD 1.05, omit FfdVersion as specified by the bank's API.
        return receipt
