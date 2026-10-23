"""Paid bot-project gateway. Code, files, messages and bot credentials stay on the owner device.

Only routing metadata and credential fingerprints are persisted. Webhook bodies exist
in memory until the local worker acknowledges durable receipt; no server-side spool.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets
import ssl
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, FastAPI, Request, WebSocket, WebSocketDisconnect

from .service import Caller, Cloud, CloudError

MAX_BODY = 256 * 1024
MAX_PENDING = 8
ACK_TIMEOUT = 15
TG_TOKEN = re.compile(r"^[0-9]{4,20}:[A-Za-z0-9_-]{20,128}$")
METHOD = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,63}$")
MAX_PATH = re.compile(r"^/(?:me|messages|chats|answers)(?:/[A-Za-z0-9_-]+)*$")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class BotCredentialFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = re.sub(r"https://api\.telegram\.org/bot[^/\s]+/", "https://api.telegram.org/bot[redacted]/", record.getMessage())
        record.args = ()
        return True


# httpx logs Telegram credentials as part of its URL even at INFO without this.
if not any(isinstance(f, BotCredentialFilter) for f in logging.getLogger("httpx").filters):
    logging.getLogger("httpx").addFilter(BotCredentialFilter())


@dataclass
class Worker:
    ws: WebSocket
    key: str
    account: str
    pending: dict[str, asyncio.Future] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, data: dict) -> None:
        async with self.lock:
            await self.ws.send_json(data)


class BotGateway:
    def __init__(self, cloud: Cloud, transport: httpx.AsyncBaseTransport | None = None):
        self.cloud = cloud
        self.transport = transport
        self.workers: dict[str, Worker] = {}
        self.rates: dict[str, deque[float]] = {}

    def plan(self, account_id: str) -> dict | None:
        current = getattr(self.cloud, "plan_state", None)
        if callable(current):
            return current(account_id)
        row = self.cloud.db.bot_gateway_subscription(account_id)
        if row is None:
            return None
        return {**row, "active": row["expires_at"] > getattr(self.cloud, "clock", time.time)()}

    def require_plan(self, caller: Caller) -> dict:
        plan = self.plan(caller.account_id)
        # No balance threshold, free welcome credit or legacy unlimited bypass.
        if not plan or not plan["active"] or not plan["connect"] or plan["price_rub"] <= 0:
            raise CloudError(
                403,
                "bot_plan_required",
                "Для шлюза ботов нужен действующий оплаченный тариф со связью между устройствами. Проекты и файлы на вашем компьютере сохраняются.",
            )
        return plan

    def owned(self, caller: Caller, project_id: str) -> dict:
        row = self.cloud.db.bot_project(project_id)
        if row is None or row["account_id"] != caller.account_id:
            raise CloudError(404, "bot_not_found", "Проект бота не найден")
        return row

    def active(self, caller: Caller, project_id: str) -> dict:
        row = self.owned(caller, project_id)
        self.require_plan(caller)
        if not row["enabled"]:
            raise CloudError(409, "bot_paused", "Шлюз этого бота приостановлен владельцем")
        return row

    def rate(self, project_id: str) -> None:
        now = time.monotonic()
        hits = self.rates.setdefault(project_id, deque())
        while hits and hits[0] < now - 60:
            hits.popleft()
        if len(hits) >= 120:
            raise CloudError(429, "bot_rate_limited", "Слишком много действий бота; повторите позже")
        hits.append(now)
        # Bounded per-process metadata, not an unbounded visitor-key cache.
        if len(self.rates) > 4096:
            self.rates.pop(next(iter(self.rates)))

    def context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        ca = Path(__file__).with_name("certificates") / "russian-root.pem"
        if ca.exists():
            ctx.load_verify_locations(ca)
        return ctx

    async def provider(
        self, platform: str, token: str, method: str, body: dict | None = None, params: dict | None = None, verb: str = "POST"
    ) -> dict:
        if platform == "telegram":
            if not TG_TOKEN.fullmatch(token) or not METHOD.fullmatch(method):
                raise CloudError(400, "bot_request_invalid", "Некорректный токен или метод Telegram")
            url = f"https://api.telegram.org/bot{token}/{method}"
            headers = {}
        else:
            if not re.fullmatch(r"[A-Za-z0-9_-]{20,256}", token) or not MAX_PATH.fullmatch(method) and method != "/subscriptions":
                raise CloudError(400, "bot_request_invalid", "Некорректный токен или путь MAX")
            url = "https://platform-api2.max.ru" + method
            headers = {"Authorization": token}
        try:
            async with httpx.AsyncClient(
                transport=self.transport, verify=self.context(), timeout=15, follow_redirects=False, trust_env=False
            ) as client:
                response = await client.request(verb, url, headers=headers, json=body, params=params)
                data = response.json()
        except (httpx.HTTPError, ValueError):
            raise CloudError(502, "bot_platform_unavailable", "Платформа бота временно недоступна") from None
        if response.status_code >= 300 or not isinstance(data, dict) or data.get("ok") is False or data.get("success") is False:
            # Never expose token-bearing provider URLs/errors or headers.
            raise CloudError(502, "bot_platform_refused", "Платформа отклонила действие. Проверьте токен и права бота в чате или канале.")
        return data

    def public(self, row: dict) -> dict:
        return {k: row[k] for k in ("id", "platform", "provider_id", "label", "enabled", "created_at")} | {
            "online": row["id"] in self.workers
        }

    async def register(self, caller: Caller, body: dict) -> dict:
        self.require_plan(caller)
        platform = body.get("platform")
        if platform not in ("telegram", "max"):
            raise CloudError(400, "bot_platform_invalid", "Поддерживаются Telegram и MAX")
        if len(self.cloud.db.bot_projects(caller.account_id)) >= 3:
            raise CloudError(409, "bot_project_limit", "Достигнут текущий лимит: три проекта ботов на аккаунт")
        base = self.cloud.s.public_base.rstrip("/")
        parts = urlsplit(base)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.port not in (None, 443)
            or parts.username
            or parts.query
            or parts.fragment
        ):
            raise CloudError(503, "bot_gateway_unconfigured", "Оператор должен настроить публичный HTTPS-адрес шлюза")
        token = str(body.get("token") or "")
        me = await self.provider(platform, token, "getMe" if platform == "telegram" else "/me", verb="GET")
        identity = me.get("result", {}) if platform == "telegram" else me
        if not identity.get("is_bot"):
            raise CloudError(400, "bot_identity_invalid", "Нужен токен зарегистрированного бота")
        provider_id = str(identity.get("id") if platform == "telegram" else identity.get("user_id") or identity.get("id") or "")
        if not provider_id or provider_id == "None":
            raise CloudError(400, "bot_identity_invalid", "Платформа не вернула идентификатор бота")
        if self.cloud.db.bot_project_for_provider(platform, provider_id):
            raise CloudError(
                409, "bot_already_registered", "Этот бот уже подключён. Используйте существующий проект или отключите его перед переносом."
            )
        project_id = "bp_" + secrets.token_hex(12)
        secret = secrets.token_urlsafe(32)
        url = base + "/v1/bot-projects/hooks/" + project_id
        if platform == "telegram":
            current = await self.provider(platform, token, "getWebhookInfo", verb="GET")
            if current.get("result", {}).get("url"):
                raise CloudError(409, "bot_webhook_exists", "У бота уже настроена доставка. Сначала явно отключите прежнюю интеграцию.")
        else:
            current = await self.provider(platform, token, "/subscriptions", verb="GET")
            if current.get("subscriptions"):
                raise CloudError(409, "bot_webhook_exists", "У бота уже настроена доставка. Сначала явно отключите прежнюю интеграцию.")
        # Reserve before changing upstream. Only metadata, never tokens or source code.
        row = self.cloud.db.add_bot_project(
            project_id,
            caller.account_id,
            platform,
            provider_id,
            str(body.get("label") or identity.get("username") or "Мой бот")[:80],
            digest(token),
            digest(secret),
        )
        try:
            if platform == "telegram":
                await self.provider(
                    platform,
                    token,
                    "setWebhook",
                    {
                        "url": url,
                        "secret_token": secret,
                        "allowed_updates": ["message", "callback_query", "channel_post", "my_chat_member"],
                        "drop_pending_updates": False,
                    },
                )
            else:
                await self.provider(
                    platform,
                    token,
                    "/subscriptions",
                    {
                        "url": url,
                        "secret": secret,
                        "update_types": ["message_created", "message_callback", "bot_started", "bot_added", "bot_removed"],
                    },
                )
        except Exception:
            # Keep reservation to prevent another account taking an ambiguously registered bot.
            self.cloud.db.set_bot_project_enabled(project_id, False)
            raise
        return self.public(row)

    async def close_worker(self, project_id: str) -> None:
        worker = self.workers.pop(project_id, None)
        if worker is not None:
            for future in worker.pending.values():
                if not future.done():
                    future.set_result(False)
            await worker.ws.close(code=4004, reason="bot_gateway_paused")


async def object_body(request: Request) -> dict:
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_BODY:
            raise CloudError(413, "bot_body_too_large", "Слишком большой запрос бота")
    try:
        value = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        raise CloudError(400, "bot_body_invalid", "Ожидается JSON-объект") from None
    if not isinstance(value, dict):
        raise CloudError(400, "bot_body_invalid", "Ожидается JSON-объект")
    return value


def install(app: FastAPI, cloud: Cloud, caller_dep: Any, transport: httpx.AsyncBaseTransport | None = None) -> BotGateway:
    gateway = BotGateway(cloud, transport)
    app.state.bot_gateway = gateway

    @app.get("/v1/bot-projects")
    async def projects(caller: Caller = Depends(caller_dep)) -> dict:
        try:
            gateway.require_plan(caller)
            allowed = True
        except CloudError:
            allowed = False
        return {
            "gateway_allowed": allowed,
            "storage": "owner_device",
            "projects": [gateway.public(r) for r in cloud.db.bot_projects(caller.account_id)],
        }

    @app.post("/v1/bot-projects")
    async def register(request: Request, caller: Caller = Depends(caller_dep)) -> dict:
        gateway.rate("register:" + caller.account_id)
        return await gateway.register(caller, await object_body(request))

    @app.post("/v1/bot-projects/{project_id}/state")
    async def state(project_id: str, request: Request, caller: Caller = Depends(caller_dep)) -> dict:
        gateway.owned(caller, project_id)
        body = await object_body(request)
        if not isinstance(body.get("enabled"), bool):
            raise CloudError(400, "bot_state_invalid", "Укажите enabled: true или false")
        if body["enabled"]:
            gateway.require_plan(caller)
        cloud.db.set_bot_project_enabled(project_id, body["enabled"])
        if not body["enabled"]:
            await gateway.close_worker(project_id)
        return gateway.public(gateway.owned(caller, project_id))

    @app.post("/v1/bot-projects/{project_id}/refresh")
    async def refresh(project_id: str, request: Request, caller: Caller = Depends(caller_dep)) -> dict:
        row = gateway.active(caller, project_id)
        gateway.rate(project_id)
        body = await object_body(request)
        token = str(body.get("token") or "")
        if not hmac.compare_digest(digest(token), row["token_hash"]):
            raise CloudError(403, "bot_token_mismatch", "Токен не соответствует проекту")
        url = cloud.s.public_base.rstrip("/") + "/v1/bot-projects/hooks/" + project_id
        if row["platform"] == "telegram":
            current = await gateway.provider("telegram", token, "getWebhookInfo", verb="GET")
            existing = current.get("result", {}).get("url")
            if existing and existing != url:
                raise CloudError(409, "bot_webhook_exists", "Бот подключён к другой интеграции")
        else:
            current = await gateway.provider("max", token, "/subscriptions", verb="GET")
            if any(item.get("url") != url for item in current.get("subscriptions", [])):
                raise CloudError(409, "bot_webhook_exists", "Бот подключён к другой интеграции")
        secret = secrets.token_urlsafe(32)
        if row["platform"] == "telegram":
            await gateway.provider(
                "telegram",
                token,
                "setWebhook",
                {
                    "url": url,
                    "secret_token": secret,
                    "allowed_updates": ["message", "callback_query", "channel_post", "my_chat_member"],
                    "drop_pending_updates": False,
                },
            )
        else:
            await gateway.provider(
                "max",
                token,
                "/subscriptions",
                {
                    "url": url,
                    "secret": secret,
                    "update_types": ["message_created", "message_callback", "bot_started", "bot_added", "bot_removed"],
                },
            )
        cloud.db.refresh_bot_project_secret(project_id, digest(secret))
        return gateway.public(gateway.owned(caller, project_id))

    @app.post("/v1/bot-projects/{project_id}/disconnect")
    async def disconnect(project_id: str, request: Request, caller: Caller = Depends(caller_dep)) -> dict:
        # Owner may disconnect even after payment ends. No deletion of local files.
        row = gateway.owned(caller, project_id)
        body = await object_body(request)
        token = str(body.get("token") or "")
        if not hmac.compare_digest(digest(token), row["token_hash"]):
            raise CloudError(403, "bot_token_mismatch", "Токен не соответствует проекту")
        cloud.db.set_bot_project_enabled(project_id, False)
        await gateway.close_worker(project_id)
        url = cloud.s.public_base.rstrip("/") + "/v1/bot-projects/hooks/" + project_id
        if row["platform"] == "telegram":
            current = await gateway.provider("telegram", token, "getWebhookInfo", verb="GET")
            if current.get("result", {}).get("url") in ("", None, url):
                await gateway.provider("telegram", token, "deleteWebhook", {"drop_pending_updates": False})
        else:
            await gateway.provider("max", token, "/subscriptions", params={"url": url}, verb="DELETE")
        cloud.db.delete_bot_project(project_id)
        gateway.rates.pop(project_id, None)
        return {"disconnected": True, "local_files_retained": True}

    @app.post("/v1/bot-projects/{project_id}/call")
    async def call(project_id: str, request: Request, caller: Caller = Depends(caller_dep)) -> dict:
        row = gateway.active(caller, project_id)
        gateway.rate(project_id)
        body = await object_body(request)
        token = str(body.get("token") or "")
        if not hmac.compare_digest(digest(token), row["token_hash"]):
            raise CloudError(403, "bot_token_mismatch", "Токен не соответствует подключённому боту")
        method = str(body.get("method") or "")
        if row["platform"] == "telegram" and method.lower() in {"setwebhook", "deletewebhook", "getupdates", "logout", "close"}:
            raise CloudError(403, "bot_gateway_managed_route", "Доставкой бота управляет шлюз nanoLika")
        if row["platform"] == "max" and (not MAX_PATH.fullmatch(method)):
            raise CloudError(403, "bot_gateway_managed_route", "Этот путь не доступен через шлюз")
        verb = str(body.get("verb") or "POST").upper()
        if verb not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            raise CloudError(400, "bot_method_invalid", "Некорректный HTTP-метод")
        payload, params = body.get("body", {}), body.get("params", {})
        if not isinstance(payload, dict) or not isinstance(params, dict):
            raise CloudError(400, "bot_body_invalid", "Параметры должны быть объектами")
        # Recheck at execution, not merely at registration/socket handshake.
        gateway.active(caller, project_id)
        return await gateway.provider(row["platform"], token, method, payload, params, verb)

    @app.post("/v1/bot-projects/hooks/{project_id}")
    async def webhook(project_id: str, request: Request) -> dict:
        row = cloud.db.bot_project(project_id)
        if row is None:
            raise CloudError(404, "bot_not_found", "Бот не найден")
        header = "X-Telegram-Bot-Api-Secret-Token" if row["platform"] == "telegram" else "X-Max-Bot-Api-Secret"
        if not hmac.compare_digest(digest(request.headers.get(header, "")), row["secret_hash"]):
            raise CloudError(403, "bot_hook_forbidden", "Неверная подпись доставки")
        gateway.rate(project_id)
        plan = gateway.plan(row["account_id"])
        if not row["enabled"] or not plan or not plan["active"] or not plan["connect"] or plan["price_rub"] <= 0:
            await gateway.close_worker(project_id)
            # A paused service does not retain messages to replay after renewal.
            return {"ok": True, "paused": True}
        worker = gateway.workers.get(project_id)
        if worker is None:
            raise CloudError(503, "bot_device_offline", "Компьютер владельца бота не подключён")
        try:
            fresh = cloud.authenticate(worker.key)
            gateway.active(fresh, project_id)
        except CloudError:
            await gateway.close_worker(project_id)
            return {"ok": True, "paused": True}
        if len(worker.pending) >= MAX_PENDING:
            raise CloudError(503, "bot_device_busy", "Компьютер пока занят; повторите доставку")
        update = await object_body(request)
        delivery = secrets.token_hex(12)
        future = asyncio.get_running_loop().create_future()
        worker.pending[delivery] = future
        try:
            await asyncio.wait_for(worker.send({"type": "update", "delivery": delivery, "update": update}), 3)
            accepted = await asyncio.wait_for(future, ACK_TIMEOUT)
            if not accepted:
                raise CloudError(503, "bot_not_accepted", "Компьютер не сохранил доставку")
        except (TimeoutError, RuntimeError, WebSocketDisconnect):
            raise CloudError(503, "bot_not_accepted", "Компьютер не подтвердил доставку") from None
        finally:
            worker.pending.pop(delivery, None)
        return {"ok": True}

    @app.websocket("/v1/bot-projects/{project_id}/socket")
    async def socket(ws: WebSocket, project_id: str) -> None:
        await ws.accept()
        worker = None
        try:
            raw = ws.headers.get("authorization", "")
            key = raw[7:].strip() if raw.lower().startswith("bearer ") else ""
            caller = cloud.authenticate(key)
            gateway.active(caller, project_id)
            if project_id in gateway.workers:
                raise CloudError(409, "bot_worker_exists", "Этот бот уже запущен на другом соединении")
            worker = Worker(ws, key, caller.account_id)
            gateway.workers[project_id] = worker
            await worker.send({"type": "ready", "storage": "owner_device"})
            while True:
                try:
                    raw_frame = await asyncio.wait_for(ws.receive_text(), 15)
                    if len(raw_frame.encode()) > MAX_BODY:
                        break
                    frame = json.loads(raw_frame)
                    gateway.rate("socket:" + project_id)
                except TimeoutError:
                    frame = {"type": "ping"}
                gateway.active(cloud.authenticate(key), project_id)
                if not isinstance(frame, dict):
                    break
                if frame.get("type") == "ack":
                    future = worker.pending.get(str(frame.get("delivery") or ""))
                    if future is not None and not future.done():
                        future.set_result(frame.get("stored") is True)
                elif frame.get("type") == "ping":
                    await worker.send({"type": "pong"})
        except CloudError as exc:
            await ws.close(code=4004, reason=exc.code)
        except (WebSocketDisconnect, RuntimeError, ValueError):
            pass
        finally:
            if worker is not None and gateway.workers.get(project_id) is worker:
                gateway.workers.pop(project_id, None)
                for future in worker.pending.values():
                    if not future.done():
                        future.set_result(False)

    return gateway
