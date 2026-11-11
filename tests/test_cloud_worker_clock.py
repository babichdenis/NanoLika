"""A shared worker must never expose one customer's clock to another."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from nanomuse import timeutil
from nanomuse.config import Settings
from nanomuse.memory.scope import memory_scope
from nanomuse.server import cloud_worker
from nanomuse.server.accounts import AccountRuntime


@pytest.mark.asyncio
async def test_worker_clocks_are_isolated_even_for_legacy_session_memory(monkeypatch):
    monkeypatch.delenv("NANOMUSE_SHARED_CLOUD_BOT", raising=False)
    timeutil.set_zone("Europe/Moscow")
    assert timeutil.set_account_zone("account:clock-a", "Europe/Kaliningrad")
    assert timeutil.set_account_zone("account:clock-b", "Asia/Vladivostok")
    ready = asyncio.Event()
    count = 0

    async def read_clock(account):
        nonlocal count
        with timeutil.account_runtime(account), memory_scope("legacy-session:one"):
            count += 1
            if count == 2:
                ready.set()
            await ready.wait()
            await asyncio.sleep(0)
            return timeutil.zone_name(), timeutil.trusted()

    assert await asyncio.gather(read_clock("clock-a"), read_clock("clock-b")) == [
        ("Europe/Kaliningrad", True), ("Asia/Vladivostok", True)
    ]
    with timeutil.account_runtime("unknown"), memory_scope("account:clock-a"):
        assert not timeutil.trusted()
        assert timeutil.zone_name() == timeutil.system_zone_name()
    assert timeutil.zone_name() == "Europe/Moscow"
    timeutil.set_zone("")


@pytest.mark.asyncio
async def test_runtime_clock_rejects_a_different_account_from_relay(monkeypatch):
    from nanomuse.hub.service import HubService

    monkeypatch.delenv("NANOMUSE_SHARED_CLOUD_BOT", raising=False)
    service = SimpleNamespace(_runtime_account_id="verified")
    hub = SimpleNamespace(svc=service)
    with timeutil.account_runtime("verified"):
        await HubService._sync_clock(hub, {"account": {"id": "verified", "tz": "Asia/Vladivostok"}})
        assert timeutil.zone_name() == "Asia/Vladivostok"
        await HubService._sync_clock(hub, {"account": {"id": "other", "tz": "Europe/Moscow"}})
        assert not timeutil.trusted()
        assert timeutil.zone_name() == timeutil.system_zone_name()
        await HubService._sync_clock(hub, {"account": {"id": "verified", "tz": "Mars/Olympus"}})
        assert not timeutil.trusted()


@pytest.mark.asyncio
async def test_worker_http_context_uses_only_the_secret_authenticated_header(monkeypatch):
    monkeypatch.setattr(cloud_worker, "WORKER_SECRET", "fixture-secret")
    timeutil.set_account_zone("account:http-a", "Europe/Kaliningrad")
    timeutil.set_account_zone("account:http-b", "Asia/Vladivostok")
    app = FastAPI()
    app.add_middleware(cloud_worker.RuntimeAccountContext)

    @app.get("/")
    async def clock():
        await asyncio.sleep(0)
        return {"account": timeutil.runtime_account(), "zone": timeutil.zone_name()}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        a, b, denied = await asyncio.gather(
            client.get("/", headers={"X-Account": "http-a", "X-Worker-Secret": "fixture-secret"}),
            client.get("/", headers={"X-Account": "http-b", "X-Worker-Secret": "fixture-secret"}),
            client.get("/", headers={"X-Account": "http-a", "X-Worker-Secret": "wrong"}),
        )
        with timeutil.account_runtime("inherited"):
            inherited = await client.get("/", headers={"X-Account": "http-a", "X-Worker-Secret": "wrong"})
            assert timeutil.runtime_account() == "inherited"
    assert a.json() == {"account": "http-a", "zone": "Europe/Kaliningrad"}
    assert b.json() == {"account": "http-b", "zone": "Asia/Vladivostok"}
    assert denied.json()["account"] == ""
    assert inherited.json()["account"] == ""
    assert timeutil.runtime_account() == ""


def test_worker_websocket_context_inherits_the_verified_account(monkeypatch):
    monkeypatch.setattr(cloud_worker, "WORKER_SECRET", "fixture-secret")
    app = FastAPI()
    app.add_middleware(cloud_worker.RuntimeAccountContext)

    @app.websocket("/ws")
    async def socket(ws: WebSocket):
        await ws.accept()
        await ws.send_json({"account": timeutil.runtime_account()})
        await ws.close()

    client = TestClient(app)
    with client.websocket_connect("/ws", headers={"X-Account": "socket-a", "X-Worker-Secret": "fixture-secret"}) as ws:
        assert ws.receive_json() == {"account": "socket-a"}
    with client.websocket_connect("/ws", headers={"X-Account": "socket-a", "X-Worker-Secret": "wrong"}) as ws:
        assert ws.receive_json() == {"account": ""}


@pytest.mark.asyncio
async def test_runtime_build_and_spawned_background_task_inherit_owner(monkeypatch, tmp_path):
    seen = []
    runtime = AccountRuntime(Settings(), tmp_path / "accounts")

    async def build(account, key=""):
        async def child():
            await asyncio.sleep(0)
            seen.append(timeutil.runtime_account())

        await asyncio.create_task(child())
        return object()

    monkeypatch.setattr(runtime, "_build", build)
    await asyncio.gather(runtime.service("build-a"), runtime.service("build-b"))
    assert sorted(seen) == ["build-a", "build-b"]
    assert timeutil.runtime_account() == ""


@pytest.mark.asyncio
async def test_real_runtime_builder_binds_owner_before_start_and_background(monkeypatch, tmp_path):
    from nanomuse.server import service as service_module

    release = asyncio.Event()
    seen = []

    class RuntimeStub:
        def __init__(self, settings):
            self.app = SimpleNamespace(tools=SimpleNamespace(remove=lambda _name: None))
            self.sync = SimpleNamespace(account_changed=lambda account: seen.append(("sync", account)))

        async def start(self):
            seen.append(("start", self._runtime_account_id, timeutil.runtime_account()))

            async def background():
                await release.wait()
                seen.append(("background", timeutil.runtime_account()))

            self.task = asyncio.create_task(background())

        async def stop(self):
            pass

    monkeypatch.setattr(service_module, "MuseService", RuntimeStub)
    runtime = AccountRuntime(Settings(), tmp_path / "accounts")
    service = await runtime.service("verified-owner")
    assert timeutil.runtime_account() == ""
    release.set()
    await service.task
    assert seen == [
        ("start", "verified-owner", "verified-owner"),
        ("sync", "verified-owner"),
        ("background", "verified-owner"),
    ]
