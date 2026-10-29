"""Robokassa hosted checkout with signed callbacks and server-side reconciliation."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import xml.etree.ElementTree as ET
from decimal import Decimal
from urllib.parse import parse_qs, quote, urlencode, urlparse

import httpx
from fastapi import Request

from .payments import Payments, cents
from .service import Cloud, CloudError

PAYMENT_URL = "https://auth.robokassa.ru/Merchant/Index.aspx"
STATE_URL = "https://auth.robokassa.ru/Merchant/WebService/Service.asmx/OpStateExt"
MAX_INVOICE_ID = 9_223_372_036_854_775_807
TAXATIONS = {"osn", "usn_income", "usn_income_outcome", "esn", "patent"}
TAXES = {"none", "vat0", "vat5", "vat7", "vat10", "vat20", "vat22", "vat105", "vat107", "vat110", "vat120", "vat122"}
PAYMENT_METHODS = {"full_prepayment", "prepayment", "advance", "full_payment", "partial_payment", "credit", "credit_payment"}
PAYMENT_OBJECTS = {
    "commodity",
    "excise",
    "job",
    "service",
    "gambling_bet",
    "gambling_prize",
    "lottery",
    "lottery_prize",
    "intellectual_activity",
    "payment",
    "agent_commission",
    "composite",
    "another",
    "property_right",
    "non-operating_gain",
    "insurance_premium",
    "sales_tax",
    "resort_fee",
}


class RobokassaPayments(Payments):
    payment_hosts = {"auth.robokassa.ru"}

    def __init__(self, cloud: Cloud, public_base: str, transport=None):
        self.provider = "robokassa"
        self.merchant_login = os.getenv("ROBOKASSA_MERCHANT_LOGIN", "").strip()
        self.test_mode = os.getenv("ROBOKASSA_TEST_MODE", "0") == "1"
        self.password1 = os.getenv("ROBOKASSA_TEST_PASSWORD_1" if self.test_mode else "ROBOKASSA_PASSWORD_1", "").strip()
        self.password2 = os.getenv("ROBOKASSA_TEST_PASSWORD_2" if self.test_mode else "ROBOKASSA_PASSWORD_2", "").strip()
        self.transport = transport
        self.webhook_url = public_base.rstrip("/") + "/v1/payments/robokassa/notify"
        suffix = ":DEMO" if self.test_mode else ""
        terminal = "robokassa:" + self.merchant_login + suffix
        super().__init__(cloud, terminal, self.password1, public_base, transport)

    @staticmethod
    def valid_payment_id(payment_id: str) -> bool:
        return payment_id.isdigit() and 1 <= int(payment_id) <= MAX_INVOICE_ID

    def ready(self):
        parsed = urlparse(self.public_base)
        if (
            not self.merchant_login
            or len(self.merchant_login) > 100
            or not self.password1
            or not self.password2
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise CloudError(503, "payment_unavailable", "Robokassa пока не настроена")
        if os.getenv("ROBOKASSA_HASH_ALGORITHM", "MD5").strip().upper() != "MD5":
            raise CloudError(503, "payment_unavailable", "Нужно настроить алгоритм подписи Robokassa MD5")
        if self.test_mode:
            return
        if os.getenv("ROBOKASSA_LIVE_ENABLED") != "1" or os.getenv("ROBOKASSA_RECEIPTS_READY") != "1":
            raise CloudError(503, "payment_live_blocked", "Приём реальной оплаты пока готовится")
        if self.receipt_errors():
            raise CloudError(503, "payment_receipt", "Не настроены данные чека Robokassa")

    def receipt_errors(self) -> list[str]:
        def get(name: str) -> str:
            return os.getenv("ROBOKASSA_RECEIPT_" + name, "").strip()

        errors = []
        if get("TAXATION") and get("TAXATION") not in TAXATIONS:
            errors.append("TAXATION")
        if not get("NAME") or len(get("NAME")) > 128 or any(ord(c) < 32 for c in get("NAME")):
            errors.append("NAME")
        if get("METHOD") and get("METHOD") not in PAYMENT_METHODS:
            errors.append("METHOD")
        if get("OBJECT") and get("OBJECT") not in PAYMENT_OBJECTS:
            errors.append("OBJECT")
        if get("TAX") not in TAXES:
            errors.append("TAX")
        return errors

    def _receipt(self, amount: int) -> str:
        if self.test_mode:
            return ""
        if self.receipt_errors():
            raise CloudError(503, "payment_receipt", "Не настроены данные чека Robokassa")

        def get(name: str) -> str:
            return os.getenv("ROBOKASSA_RECEIPT_" + name, "").strip()

        item = {
            "name": get("NAME"),
            "quantity": 1,
            "sum": str(Decimal(amount) / 100),
            "tax": get("TAX"),
        }
        if get("METHOD"):
            item["payment_method"] = get("METHOD")
        if get("OBJECT"):
            item["payment_object"] = get("OBJECT")
        receipt = {"items": [item]}
        if get("TAXATION"):
            receipt["sno"] = get("TAXATION")
        return json.dumps(receipt, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _signature(value: str) -> str:
        return hashlib.md5(value.encode("utf-8"), usedforsecurity=False).hexdigest()

    @staticmethod
    def _rub(amount: int) -> str:
        return f"{Decimal(amount) / 100:.2f}"

    def _invoice_for_order(self, order_id: str) -> int:
        with self.db.tx() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS robokassa_invoices ("
                "invoice_id INTEGER PRIMARY KEY, order_id TEXT NOT NULL UNIQUE REFERENCES tbank_orders(id))"
            )
            old = db.execute("SELECT invoice_id FROM robokassa_invoices WHERE order_id=?", (order_id,)).fetchone()
            if old:
                return int(old["invoice_id"])
            for _ in range(5):
                candidate = secrets.randbelow(MAX_INVOICE_ID) + 1
                try:
                    db.execute("INSERT INTO robokassa_invoices(invoice_id,order_id) VALUES(?,?)", (candidate, order_id))
                    return candidate
                except sqlite3.IntegrityError as exc:
                    if "UNIQUE constraint failed" not in str(exc):
                        raise
            raise CloudError(503, "payment_invoice", "Не удалось создать номер счёта Robokassa")

    def _order_for_invoice(self, invoice_id: str) -> str:
        if not self.valid_payment_id(invoice_id):
            raise CloudError(400, "payment_event", "Некорректный номер счёта")
        with self.db.tx() as db:
            row = db.execute("SELECT order_id FROM robokassa_invoices WHERE invoice_id=?", (int(invoice_id),)).fetchone()
        if row is None:
            raise CloudError(404, "payment_missing", "Заказ не найден")
        return str(row["order_id"])

    def _payment_url(self, order_id: str, invoice_id: int, amount: int) -> str:
        out_sum = self._rub(amount)
        receipt = self._receipt(amount)
        receipt_encoded = quote(receipt, safe="") if receipt else ""
        custom = {"Shp_order": order_id}
        parts = [self.merchant_login, out_sum, str(invoice_id)]
        if receipt:
            parts.append(receipt_encoded)
        parts.append(self.password1)
        parts.extend(f"{key}={value}" for key, value in sorted(custom.items()))
        values = {
            "MerchantLogin": self.merchant_login,
            "OutSum": out_sum,
            "InvId": str(invoice_id),
            "Description": "Пополнение баланса nanoLika",
            "SignatureValue": self._signature(":".join(parts)),
            "Culture": "ru",
            "IsTest": "1" if self.test_mode else "0",
            **custom,
        }
        query = urlencode(values)
        if receipt_encoded:
            query += "&Receipt=" + receipt_encoded
        return PAYMENT_URL + "?" + query

    async def bank(self, method: str, payload: dict) -> dict:
        self.ready()
        if method == "Init":
            order_id = str(payload.get("OrderId", ""))
            amount = payload.get("Amount")
            if not order_id or type(amount) is not int or amount < 1:
                raise CloudError(400, "payment_event", "Некорректный заказ")
            invoice_id = self._invoice_for_order(order_id)
            return {
                "OrderId": order_id,
                "Amount": amount,
                "PaymentId": str(invoice_id),
                "PaymentURL": self._payment_url(order_id, invoice_id, amount),
                "Success": True,
                "TerminalKey": self.terminal,
                "Status": "NEW",
            }
        if method == "CheckOrder":
            order_id = str(payload.get("OrderId", ""))
            with self.db.tx() as db:
                row = db.execute("SELECT invoice_id FROM robokassa_invoices WHERE order_id=?", (order_id,)).fetchone()
            items = []
            if row:
                order = self.order(order_id)
                items = [{"PaymentId": str(row["invoice_id"]), "Amount": order["amount"]}]
            return {"TerminalKey": self.terminal, "OrderId": order_id, "Payments": items}
        if method == "GetState":
            return await self._state(str(payload.get("PaymentId", "")))
        raise CloudError(400, "payment_method", "Неизвестная операция оплаты")

    async def _state(self, invoice_id: str) -> dict:
        if not self.valid_payment_id(invoice_id):
            raise CloudError(400, "payment_event", "Некорректный номер счёта")
        order_id = self._order_for_invoice(invoice_id)
        row = self.order(order_id)
        if self.test_mode:
            status = "CONFIRMED" if row["credited"] else row["status"]
            return {
                "TerminalKey": self.terminal,
                "OrderId": order_id,
                "PaymentId": invoice_id,
                "Amount": row["amount"],
                "Success": row["credited"],
                "Status": status,
            }
        signature = self._signature(f"{self.merchant_login}:{invoice_id}:{self.password2}")
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=15, trust_env=False, follow_redirects=False) as client:
                response = await client.get(
                    STATE_URL,
                    params={"MerchantLogin": self.merchant_login, "InvoiceID": invoice_id, "Signature": signature},
                )
                response.raise_for_status()
            if len(response.content) > 131072:
                raise ValueError("state_size")
            root = ET.fromstring(response.content)
            code = self._xml_text(root, "Code", parent="Result")
            state = self._xml_text(root, "Code", parent="State")
        except (httpx.HTTPError, ET.ParseError, ValueError):
            raise CloudError(502, "payment_provider", "Robokassa не подтвердила статус. Проверьте заказ перед повтором") from None
        if code == "3":
            status, success = "NEW", False
        elif code != "0":
            raise CloudError(502, "payment_provider", "Robokassa не подтвердила статус. Проверьте заказ перед повтором")
        else:
            status, success = {
                "5": ("NEW", False),
                "10": ("CANCELED", False),
                "20": ("AUTHORIZED", False),
                "50": ("PROCESSING", False),
                "60": ("REJECTED", False),
                "80": ("PROCESSING", False),
                "100": ("CONFIRMED", True),
            }.get(state, ("UNKNOWN", False))
            if status == "UNKNOWN":
                raise CloudError(502, "payment_provider", "Неизвестный статус Robokassa")
        return {
            "TerminalKey": self.terminal,
            "OrderId": order_id,
            "PaymentId": invoice_id,
            "Amount": row["amount"],
            "Success": success,
            "Status": status,
        }

    @staticmethod
    def _xml_text(root: ET.Element, name: str, *, parent: str | None = None) -> str:
        roots = [root] if parent is None else [item for item in root.iter() if item.tag.rsplit("}", 1)[-1] == parent]
        for container in roots:
            for element in container.iter():
                if element.tag.rsplit("}", 1)[-1] == name:
                    return (element.text or "").strip()
        return ""

    def verify_result(self, fields: dict[str, str]) -> dict:
        if not self.merchant_login or not self.password2:
            raise CloudError(503, "payment_unavailable", "Robokassa пока не настроена")
        invoice_id = fields.get("InvId", "")
        out_sum = fields.get("OutSum", "")
        supplied = fields.get("SignatureValue", "").lower()
        custom = {key: value for key, value in fields.items() if key.startswith("Shp_")}
        if not self.valid_payment_id(invoice_id) or not supplied or "Shp_order" not in custom:
            raise CloudError(400, "payment_event", "Некорректное уведомление Robokassa")
        signed = ":".join([out_sum, invoice_id, self.password2, *(f"{key}={value}" for key, value in sorted(custom.items()))])
        if not hmac.compare_digest(supplied, self._signature(signed)):
            raise CloudError(403, "payment_signature", "Неверная подпись уведомления Robokassa")
        order_id = custom["Shp_order"]
        if self._order_for_invoice(invoice_id) != order_id:
            raise CloudError(409, "payment_mismatch", "Данные заказа Robokassa не совпали")
        row = self.order(order_id)
        try:
            amount = cents(out_sum)
        except CloudError:
            raise CloudError(400, "payment_event", "Некорректная сумма уведомления Robokassa") from None
        if amount != row["amount"]:
            raise CloudError(409, "payment_mismatch", "Сумма заказа Robokassa не совпала")
        return {
            "TerminalKey": self.terminal,
            "OrderId": order_id,
            "PaymentId": invoice_id,
            "Amount": amount,
            "Success": True,
            "Status": "CONFIRMED",
        }

    async def handle_result(self, request: Request) -> str:
        raw = await request.body()
        if len(raw) > 32768:
            raise CloudError(413, "payment_event", "Слишком большое уведомление")
        try:
            decoded = raw.decode("utf-8")
            values = parse_qs(decoded, keep_blank_values=True, strict_parsing=True)
        except (UnicodeDecodeError, ValueError):
            raise CloudError(400, "payment_event", "Некорректное уведомление Robokassa") from None
        if any(len(items) != 1 for items in values.values()):
            raise CloudError(400, "payment_event", "Некорректное уведомление Robokassa")
        event = self.verify_result({key: items[0] for key, items in values.items()})
        self.apply(event)
        return "OK" + str(int(event["PaymentId"]))


def configured_robokassa(cloud: Cloud, public_base: str, transport=None) -> RobokassaPayments:
    return RobokassaPayments(cloud, public_base, transport=transport)
