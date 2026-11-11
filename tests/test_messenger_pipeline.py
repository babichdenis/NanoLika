"""Messenger gateway and worker keep two customers' turns separate."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import PropertyMock

import httpx
import pytest
from fastapi import FastAPI
from nanomuse_cloud import bot_runtime

from nanomuse.llm import MockLLM
from nanomuse.schema import LLMResponse
from nanomuse.server import cloud_worker
from nanomuse.server.accounts import AccountRuntime
from nanomuse.server.service import MuseService


@pytest.mark.asyncio
async def test_two_accounts_through_relay_and_worker(settings, tmp_path, monkeypatch):
    account_a, account_b = str(uuid.uuid4()), str(uuid.uuid4())
    monkeypatch.setenv("NANOMUSE_ACCOUNTS_DIR", str(tmp_path / "journal"))
    monkeypatch.setattr(cloud_worker, "WORKER_SECRET", "worker-secret")
    monkeypatch.setattr(cloud_worker, "MESSENGER_SECRET", "messenger-secret-32-characters-long")
    monkeypatch.setattr(bot_runtime, "WORKER_SECRET", "worker-secret")
    monkeypatch.setattr(bot_runtime, "MESSENGER_SECRET", "messenger-secret-32-characters-long")
    services, models = {}, {}
    for label, account in (("a", account_a), ("b", account_b)):
        config = settings.model_copy(deep=True)
        config.data_dir = tmp_path / label / "data"
        config.agent.workspace = tmp_path / label / "workspace"
        config.ensure_dirs()
        model = MockLLM([LLMResponse(content=f"answer-{label}")])
        service = MuseService(config, llm=model)
        thread_id = f"local-{label}"
        service.timeline(thread_id)
        service.sync.state["account_id"] = account
        service.sync.state["owners"][thread_id] = account
        service.sync.state["cids"][thread_id] = "common-cloud-cid"
        monkeypatch.setattr(type(service.sync), "active", PropertyMock(return_value=True))
        monkeypatch.setattr(service.sync, "_eligible", lambda _thread: True)
        monkeypatch.setattr(service.sync, "message_sent", lambda _thread: None)
        monkeypatch.setattr(service.sync, "turn_finished", lambda _thread: None)
        monkeypatch.setattr(service.sync, "drop_turn_lease", lambda *_args: None)

        async def pull():
            return None

        async def hydrate(*_args, **_kwargs):
            return None

        async def lease(_thread):
            return "common-cloud-cid", "turn"

        monkeypatch.setattr(service.sync, "pull", pull)
        monkeypatch.setattr(service.sync, "hydrate_thread", hydrate)
        monkeypatch.setattr(service, "_lease_turn_strict", lease)
        monkeypatch.setattr(service, "cloud_once", lambda *_args, model=model, **_kwargs: (model, "test-model"))
        services[account], models[account] = service, model

    async def account_service(_self, account, _key):
        return services[account]

    monkeypatch.setattr(AccountRuntime, "service", account_service)
    worker = cloud_worker.build_worker()
    worker_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=worker), base_url="http://worker")

    class Cloud:
        s = SimpleNamespace(bot_signin_token="bot-secret")

        def _chat_key(self, _surface, ident):
            return ident

        def _account_for(self, ident):
            return {"id": account_a if ident.value == "12345" else account_b}

        def authenticate(self, key):
            if key not in ("key-a", "key-b"):
                raise ValueError("unknown key")
            return SimpleNamespace(account_id=account_a if key == "key-a" else account_b, via="telegram")

    relay = FastAPI()
    bot_runtime.install_bot_runtime(relay, Cloud())
    relay_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=relay), base_url="http://relay")

    class WorkerTransport:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method, url, **kwargs):
            return await worker_client.request(method, url, **kwargs)

    monkeypatch.setattr(bot_runtime.httpx, "AsyncClient", WorkerTransport)
    async def turn(label, sender):
        headers = {"X-Bot-Token": "bot-secret", "Authorization": f"Bearer key-{label}"}
        body = {"channel": "telegram", "sender_id": sender, "operation_id": f"telegram_message_{label}_123", "conversation_id": "common-cloud-cid", "text": f"question-{label}"}
        result = await relay_client.post("/v1/bot/runtime/message", json=body, headers=headers)
        return headers, body, result

    try:
        a, b = await asyncio.gather(turn("a", "12345"), turn("b", "54321"))
        assert a[2].status_code == b[2].status_code == 200
        await asyncio.gather(*(service.threads[f"local-{label}"].worker for label, service in (("a", services[account_a]), ("b", services[account_b]))))
        for label, (headers, body, response) in (("a", a), ("b", b)):
            status = await relay_client.post("/v1/bot/runtime/status", json=body, headers=headers)
            assert status.json()["state"] == "completed" and status.json()["text"] == f"answer-{label}"
            repeat = await relay_client.post("/v1/bot/runtime/message", json=body, headers=headers)
            assert repeat.json() == response.json()
        assert len(models[account_a].calls) == len(models[account_b].calls) == 1
        assert "question-b" not in str(services[account_a].threads["local-a"].timeline.events)
        assert "question-a" not in str(services[account_b].threads["local-b"].timeline.events)
    finally:
        await relay_client.aclose()
        await worker_client.aclose()
