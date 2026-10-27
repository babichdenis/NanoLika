"""YooKassa redirect checkout, sharing the relay's isolated DEMO and live ledger.

YooKassa notifications are not signed. A notification is only a hint: the relay fetches
the payment (and refund list) over authenticated HTTPS before it can settle an order.
Live keys remain disabled until both the operator switch and receipt workflow are verified.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from urllib.parse import quote, urlparse

import httpx
from fastapi import Request

from .payments import Payments, cents
from .service import Cloud, CloudError

TAX_CODES = {
    "none": 1,
    "vat0": 2,
    "vat10": 3,
    "vat22": 11,
    "vat5": 7,
    "vat7": 8,
    "vat105": 9,
    "vat107": 10,
    "vat122": 12,
}
PAYMENT_HOSTS = {"yookassa.ru", "yoomoney.ru"}


class YooKassaPayments(Payments):
    """Payment adapter for YooKassa's REST API v3."""

    payment_hosts = PAYMENT_HOSTS

    @staticmethod
    def valid_payment_id(payment_id: str) -> bool:
        return bool(payment_id) and len(payment_id) <= 80 and payment_id.isascii()

    def __init__(self, cloud: Cloud, shop_id: str, secret_key: str, public_base: str, transport=None):
        normalized_id = shop_id.strip()
        is_test = secret_key.startswith("test_")
        terminal = f"yookassa:{normalized_id}" + (":DEMO" if is_test else "")
        super().__init__(cloud, terminal, "", public_base, transport)
        self.shop_id = normalized_id
        self.secret_key = secret_key.strip()
        self.transport = transport
        self.provider = "yookassa"
        self.test_mode = is_test
        self.webhook_url = self.public_base + "/v1/payments/yookassa/notify"

    def ready(self):
        parsed = urlparse(self.public_base)
        if (
            not self.shop_id.isdigit()
            or not self.secret_key.startswith(("test_", "live_"))
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise CloudError(503, "payment_unavailable", "Оплата YooKassa пока не настроена")
        if not self.test_mode and (os.getenv("YOOKASSA_LIVE_ENABLED") != "1" or os.getenv("YOOKASSA_RECEIPTS_READY") != "1"):
            raise CloudError(503, "payment_live_blocked", "Приём оплаты пока готовится")

    def view(self, row: dict) -> dict:
        result = super().view(row)
        result["test_mode"] = self.test_mode
        result["provider"] = self.provider
        return result

    def admin_view(self, row: dict) -> dict:
        result = super().admin_view(row)
        result["test_mode"] = self.test_mode
        result["mode"] = "demo" if self.test_mode else "live"
        result["provider"] = self.provider
        return result

    async def _request(self, method: str, path: str, *, payload: dict | None = None, idempotency: str = "") -> dict:
        self.ready()
        headers = {"Idempotence-Key": idempotency} if idempotency else {}
        try:
            async with httpx.AsyncClient(
                auth=(self.shop_id, self.secret_key),
                transport=self.transport,
                timeout=20,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                response = await client.request(method, "https://api.yookassa.ru/v3" + path, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError):
            # Provider response bodies and credentials are deliberately excluded from errors/logs.
            raise CloudError(502, "payment_provider", "YooKassa не подтвердила операцию. Проверьте заказ перед повтором") from None
        if not isinstance(data, dict):
            raise CloudError(502, "payment_provider", "YooKassa вернула некорректный ответ")
        return data

    @staticmethod
    def _rub(amount: int) -> str:
        return f"{Decimal(amount) / 100:.2f}"

    def _provider_receipt(self, receipt: dict) -> dict:
        customer = receipt.get("Email")
        items = receipt.get("Items")
        if not isinstance(customer, str) or not isinstance(items, list) or not items:
            raise CloudError(400, "payment_receipt", "Не хватает данных для чека")
        converted = []
        for item in items:
            tax_code = TAX_CODES.get(str(item.get("Tax", "")))
            if tax_code is None:
                raise CloudError(400, "payment_receipt", "Для чека не настроена ставка")
            amount = item.get("Amount")
            if type(amount) is not int or amount < 1:
                raise CloudError(400, "payment_receipt", "Некорректная сумма в чеке")
            converted.append(
                {
                    "description": str(item.get("Name", ""))[:128],
                    "quantity": "1.000",
                    "amount": {"value": self._rub(amount), "currency": "RUB"},
                    "vat_code": tax_code,
                    "payment_mode": str(item.get("PaymentMethod", "")),
                    "payment_subject": str(item.get("PaymentObject", "")),
                }
            )
        return {"customer": {"email": customer}, "items": converted, "internet": "true"}

    async def _init(self, order_id: str, amount: int, receipt: dict | None = None) -> dict:
        payload = {
            "amount": {"value": self._rub(amount), "currency": "RUB"},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": self.public_base + "/payments/checkout?order=" + order_id},
            "description": "Пополнение баланса nanoLika",
            "metadata": {"order_id": order_id},
        }
        if receipt is not None:
            payload["receipt"] = self._provider_receipt(receipt)
        return await self._request("POST", "/payments", payload=payload, idempotency=order_id)

    @staticmethod
    def _status(status: str) -> str:
        return {
            "pending": "NEW",
            "waiting_for_capture": "AUTHORIZED",
            "succeeded": "CONFIRMED",
            "canceled": "CANCELED",
        }.get(status, "UNKNOWN")

    async def _refund_total(self, payment_id: str) -> int:
        data = await self._request("GET", "/refunds?payment_id=" + quote(payment_id, safe="") + "&limit=100")
        rows = data.get("items", [])
        if not isinstance(rows, list):
            raise CloudError(502, "payment_refund_state", "Не удалось сверить возврат")
        # Never settle from a partial refund list: that could misstate the remaining balance.
        if data.get("has_next") is True:
            raise CloudError(502, "payment_refund_state", "Слишком много возвратов для безопасной сверки")
        total = 0
        for item in rows:
            if not isinstance(item, dict) or item.get("status") != "succeeded":
                continue
            amount = item.get("amount", {}).get("value") if isinstance(item.get("amount"), dict) else None
            try:
                total += cents(str(amount))
            except CloudError:
                raise CloudError(502, "payment_refund_state", "Не удалось сверить сумму возврата") from None
        return total

    async def _normalize(self, data: dict, *, with_refunds: bool = False) -> dict:
        payment_id = data.get("id")
        metadata = data.get("metadata")
        amount_obj = data.get("amount")
        recipient = data.get("recipient")
        if recipient is not None and not isinstance(recipient, dict):
            raise CloudError(409, "payment_mismatch", "Данные магазина не совпали")
        if (
            not isinstance(payment_id, str)
            or not payment_id
            or len(payment_id) > 80
            or not isinstance(metadata, dict)
            or not isinstance(metadata.get("order_id"), str)
            or not isinstance(amount_obj, dict)
            or amount_obj.get("currency") != "RUB"
            or (recipient is not None and recipient.get("account_id") not in (None, self.shop_id))
        ):
            raise CloudError(409, "payment_mismatch", "Данные платежа не совпали")
        try:
            amount = cents(str(amount_obj.get("value", "")))
        except CloudError:
            raise CloudError(409, "payment_mismatch", "Некорректная сумма платежа") from None
        status = str(data.get("status", ""))
        normalized = {
            "TerminalKey": self.terminal,
            "OrderId": metadata["order_id"],
            "PaymentId": payment_id,
            "Amount": amount,
            "Success": status == "succeeded",
            "Status": self._status(status),
        }
        if with_refunds and status == "succeeded":
            refunded = await self._refund_total(payment_id)
            if refunded:
                if refunded > amount:
                    raise CloudError(409, "payment_refund_state", "Сумма возврата больше платежа")
                normalized["Status"] = "REFUNDED" if refunded == amount else "PARTIAL_REFUNDED"
                normalized["Amount"] = amount - refunded
        return normalized

    async def bank(self, method: str, payload: dict) -> dict:
        if method == "Init":
            data = await self._init(str(payload.get("OrderId", "")), int(payload.get("Amount", 0)), payload.get("Receipt"))
        elif method == "GetState":
            payment_id = str(payload.get("PaymentId", ""))
            data = await self._request("GET", "/payments/" + quote(payment_id, safe=""))
            return await self._normalize(data, with_refunds=True)
        elif method == "CheckOrder":
            order_id = str(payload.get("OrderId", ""))
            row = self.order(order_id)
            data = await self._init(order_id, int(row["amount"]))
        else:
            raise CloudError(400, "payment_method", "Неизвестная операция оплаты")

        confirmation = data.get("confirmation")
        url = confirmation.get("confirmation_url") if isinstance(confirmation, dict) else ""
        amount_obj = data.get("amount")
        try:
            result_amount = cents(str(amount_obj.get("value", ""))) if isinstance(amount_obj, dict) else 0
        except CloudError:
            result_amount = 0
        result = await self._normalize(data)
        if (
            result["OrderId"] != str(payload.get("OrderId", ""))
            or result_amount != int(payload.get("Amount", result_amount))
            or not isinstance(url, str)
        ):
            raise CloudError(502, "payment_mismatch", "YooKassa вернула несовпадающие данные заказа")
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in PAYMENT_HOSTS or parsed.username or parsed.port not in (None, 443):
            raise CloudError(502, "payment_url", "YooKassa вернула недопустимую страницу оплаты")
        return {
            **result,
            "PaymentURL": url,
            **({"Payments": [{"PaymentId": result["PaymentId"], "Amount": result_amount}]} if method == "CheckOrder" else {}),
        }

    def validate_event(self, data: dict, *, signed: bool = False) -> dict:
        # `data` reaches here only after an authenticated GET to YooKassa. Its notifications
        # are unsigned, so caller-side JSON must never be passed directly to apply().
        if data.get("TerminalKey") != self.terminal:
            raise CloudError(403, "payment_shop", "Другой магазин")
        payment_id = data.get("PaymentId")
        if not isinstance(payment_id, str) or not payment_id or len(payment_id) > 80:
            raise CloudError(400, "payment_event", "Некорректное уведомление")
        row = self.order(str(data.get("OrderId", "")))
        refund = data.get("Status") in {"REFUNDED", "PARTIAL_REFUNDED"}
        amount = data.get("Amount")
        amount_ok = type(amount) is int and (0 <= amount <= row["amount"] if refund else amount == row["amount"])
        if row["terminal"] != self.terminal or not amount_ok or (row["payment_id"] and row["payment_id"] != payment_id):
            raise CloudError(409, "payment_mismatch", "Данные платежа не совпали")
        return row

    async def handle_notification(self, request: Request) -> None:
        raw = await request.body()
        if len(raw) > 32768:
            raise CloudError(413, "payment_event", "Слишком большое уведомление")
        try:
            data = json.loads(raw)
        except ValueError:
            raise CloudError(400, "payment_event", "Некорректное уведомление") from None
        if (
            not isinstance(data, dict)
            or data.get("type") != "notification"
            or data.get("event")
            not in {
                "payment.succeeded",
                "payment.canceled",
                "refund.succeeded",
            }
        ):
            raise CloudError(400, "payment_event", "Неизвестное уведомление")
        obj = data.get("object")
        if not isinstance(obj, dict) or not isinstance(obj.get("id"), str):
            raise CloudError(400, "payment_event", "Некорректное уведомление")
        if data["event"] == "refund.succeeded":
            refund = await self._request("GET", "/refunds/" + quote(obj["id"], safe=""))
            payment_id = refund.get("payment_id")
            if not isinstance(payment_id, str):
                raise CloudError(409, "payment_event", "Возврат не связан с платежом")
        else:
            payment_id = obj["id"]
        state = await self.bank("GetState", {"PaymentId": payment_id})
        row = self.validate_event(state)
        if row["payment_id"] and row["payment_id"] != payment_id:
            raise CloudError(409, "payment_mismatch", "Данные платежа не совпали")
        self.apply(state, reconciled=True)


def configured_yookassa(cloud: Cloud, public_base: str, transport=None) -> YooKassaPayments:
    return YooKassaPayments(
        cloud,
        os.getenv("YOOKASSA_SHOP_ID", ""),
        os.getenv("YOOKASSA_SECRET_KEY", ""),
        public_base,
        transport=transport,
    )
