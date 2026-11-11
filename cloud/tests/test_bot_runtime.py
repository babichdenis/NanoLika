"""Only the verified messenger/account pair can reach the private worker."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from nanomuse_cloud import bot_runtime


@pytest.mark.asyncio
async def test_gateway_binds_bot_token_account_key_and_sender(monkeypatch):
    seen = []
    monkeypatch.setattr(bot_runtime, "WORKER_SECRET", "worker-secret")
    monkeypatch.setattr(bot_runtime, "MESSENGER_SECRET", "messenger-secret-32-characters-long")

    class Cloud:
        s = SimpleNamespace(bot_signin_token="bot-secret")

        def _chat_key(self, _surface, ident):
            return ident

        def _account_for(self, ident):
            return {"id": "account-a" if ident.value == "12345" else "account-b"}

        def authenticate(self, key):
            if key != "customer-key":
                raise ValueError("invalid key")
            return SimpleNamespace(account_id="account-a", via="telegram")

    class Worker:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method, url, **kwargs):
            seen.append((method, url, kwargs))
            return httpx.Response(200, json={"state": "accepted"})

    app = FastAPI()
    bot_runtime.install_bot_runtime(app, Cloud())
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://relay")
    monkeypatch.setattr(bot_runtime.httpx, "AsyncClient", Worker)
    body = {"channel": "telegram", "sender_id": "12345", "operation_id": "telegram_message_123", "conversation_id": "cid", "text": "hello"}
    headers = {"X-Bot-Token": "bot-secret", "Authorization": "Bearer customer-key"}
    try:
        assert (await client.post("/v1/bot/runtime/message", json=body)).status_code == 403
        assert (await client.post("/v1/bot/runtime/message", headers=headers, json={**body, "sender_id": "54321"})).status_code == 403
        assert (await client.post("/v1/bot/runtime/message", headers=headers, json={**body, "channel": "max"})).status_code == 403
        assert (await client.post("/v1/bot/runtime/message", headers={**headers, "Authorization": "Bearer wrong"}, json=body)).status_code == 403
        response = await client.post("/v1/bot/runtime/message", headers=headers, json=body)
        assert response.status_code == 200
        method, url, kwargs = seen[0]
        assert method == "POST" and url.endswith("/internal/messenger/messages")
        assert kwargs["headers"]["X-Account"] == "account-a"
        assert kwargs["headers"]["X-Cloud-Key"] == "customer-key"
        assert "sender_id" not in kwargs["json"] and "channel" not in kwargs["json"]
        result = await client.post("/v1/bot/runtime/status", headers=headers, json=body)
        assert result.status_code == 200 and seen[-1][0] == "GET"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_messenger_relay_limits_chunked_body_before_json_parse(monkeypatch):
    monkeypatch.setattr(bot_runtime, "WORKER_SECRET", "worker-secret")
    monkeypatch.setattr(bot_runtime, "MESSENGER_SECRET", "messenger-secret-32-characters-long")
    cloud = SimpleNamespace(s=SimpleNamespace(bot_signin_token="bot-secret"))
    app = FastAPI()
    bot_runtime.install_bot_runtime(app, cloud)
    async def body():
        for _ in range(16):
            yield b"x" * (1024 * 1024)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://relay") as client:
        response = await client.post("/v1/bot/runtime/message", headers={"X-Bot-Token": "bot-secret"}, content=body())
    assert response.status_code == 413
