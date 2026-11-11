"""The worker accepts forwarded accounts only from the authenticated relay."""

from __future__ import annotations

import secrets
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from nanomuse.config import Settings
from nanomuse.server import cloud_worker
from nanomuse.server.accounts import AccountRuntime


@pytest.mark.asyncio
async def test_unconfigured_worker_retains_legacy_account_check(monkeypatch):
    monkeypatch.setattr(cloud_worker, "WORKER_SECRET", "")
    app = cloud_worker.build_worker()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://worker") as client:
        assert (await client.get("/api/models")).status_code == 401


@pytest.mark.asyncio
async def test_worker_secret_required_for_account_http(monkeypatch):
    secret = secrets.token_urlsafe(32)
    monkeypatch.setattr(cloud_worker, "WORKER_SECRET", secret)

    async def fake_service(self, account_id, cloud_key=""):
        assert account_id == "fixture-account"
        async def models():
            return {"models": []}
        return SimpleNamespace(
            settings=SimpleNamespace(llm=SimpleNamespace(provider="fixture", model="fixture", cloud=False)),
            hub=SimpleNamespace(cloud=SimpleNamespace(me_models=models), models=[]),
        )

    monkeypatch.setattr(AccountRuntime, "service", fake_service)
    app = cloud_worker.build_worker()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://worker") as client:
        headers = {"X-Account": "fixture-account"}
        assert (await client.get("/api/models", headers=headers)).status_code == 403
        assert (await client.get("/api/models", headers={**headers, "X-Worker-Secret": "wrong"})).status_code == 403
        assert (await client.get("/api/probe", headers=headers)).status_code == 403
        assert (await client.get("/api/models", headers={**headers, "X-Worker-Secret": secret})).json()["current"]["model"] == "fixture"


@pytest.mark.asyncio
async def test_worker_does_not_forward_internal_secret_to_the_runtime(monkeypatch):
    secret = secrets.token_urlsafe(32)
    monkeypatch.setattr(cloud_worker, "WORKER_SECRET", secret)
    runtime_app = FastAPI()

    @runtime_app.get("/api/probe")
    async def probe(request: Request):
        return {"has_worker_secret": "x-worker-secret" in request.headers}

    class RuntimeStub:
        settings = Settings()

    async def fake_service(self, account_id, cloud_key=""):
        assert account_id == "fixture-account"
        return RuntimeStub()

    monkeypatch.setattr(AccountRuntime, "service", fake_service)
    monkeypatch.setattr(AccountRuntime, "token", lambda self, account_id: "fixture-runtime-token")
    monkeypatch.setattr(cloud_worker, "create_app", lambda settings, service: runtime_app)
    app = cloud_worker.build_worker()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://worker") as client:
        response = await client.get("/api/probe", headers={"X-Account": "fixture-account", "X-Worker-Secret": secret})
    assert response.status_code == 200
    assert response.json() == {"has_worker_secret": False}


def test_worker_secret_required_for_websocket(monkeypatch):
    secret = secrets.token_urlsafe(32)
    monkeypatch.setattr(cloud_worker, "WORKER_SECRET", secret)
    app = cloud_worker.build_worker()
    client = TestClient(app)
    # No service starts: success of the worker secret is observed by the next,
    # independent check rejecting the deliberately absent account id.
    with pytest.raises(WebSocketDisconnect) as missing:
        with client.websocket_connect("/ws"):
            pass
    assert missing.value.code == 4403
    with pytest.raises(WebSocketDisconnect) as accepted_secret:
        with client.websocket_connect("/ws", headers={"X-Worker-Secret": secret}):
            pass
    assert accepted_secret.value.code == 4401
