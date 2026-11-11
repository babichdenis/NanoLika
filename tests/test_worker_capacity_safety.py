"""A full cloud worker must refuse new work rather than kill a customer's run."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, WebSocketDisconnect

from nanomuse.config import Settings
from nanomuse.server import cloud_worker
from nanomuse.server.accounts import AccountRuntime, CapacityError
from nanomuse.server.cloud_worker import AppCache
from nanomuse.server.events import EventBus


class FakeService:
    def __init__(self):
        self.threads = {}
        self.settings = Settings()
        self.stopped = False

    async def stop(self):
        self.stopped = True


@pytest.mark.asyncio
async def test_twenty_five_accounts_do_not_evict_twenty_four_active_runs(tmp_path):
    runtime = AccountRuntime(Settings(), tmp_path, limit=24)

    async def build(account, key=""):
        service = FakeService()
        service.threads["thread"] = SimpleNamespace(busy=True)
        return service

    runtime._build = build
    services = [await runtime.service(f"account-{n}") for n in range(24)]
    with pytest.raises(CapacityError):
        await runtime.service("account-25")
    assert len(runtime.active()) == 24
    assert not any(service.stopped for service in services)


@pytest.mark.asyncio
async def test_concurrent_builds_respect_the_global_limit(tmp_path):
    runtime = AccountRuntime(Settings(), tmp_path, limit=1)

    async def build(account, key=""):
        await asyncio.sleep(0.05)
        return FakeService()

    runtime._build = build
    results = await asyncio.gather(*(runtime.service(f"account-{n}") for n in range(25)), return_exceptions=True)
    assert sum(isinstance(result, FakeService) for result in results) == 1
    assert sum(isinstance(result, CapacityError) for result in results) == 24
    assert len(runtime.active()) == 1


@pytest.mark.asyncio
async def test_waiting_inbox_is_busy_even_before_the_worker_starts(tmp_path):
    runtime = AccountRuntime(Settings(), tmp_path, limit=1)

    async def build(account, key=""):
        service = FakeService()
        inbox = asyncio.Queue()
        inbox.put_nowait("pending")
        service.threads["thread"] = SimpleNamespace(busy=False, inbox=inbox)
        return service

    runtime._build = build
    first = await runtime.service("account-a")
    with pytest.raises(CapacityError):
        await runtime.service("account-b")
    assert not first.stopped


@pytest.mark.asyncio
async def test_busy_http_request_is_not_evicted_and_returns_503_for_the_next(monkeypatch, tmp_path):
    runtime = AccountRuntime(Settings(), tmp_path, limit=1)

    async def build(account, key=""):
        return FakeService()

    runtime._build = build
    monkeypatch.setattr(runtime, "token", lambda account: "fixture-runtime-token")
    started, release = asyncio.Event(), asyncio.Event()
    child = FastAPI()

    @child.get("/api/probe")
    async def probe():
        started.set()
        await release.wait()
        return {"ok": True}

    monkeypatch.setattr(cloud_worker, "WORKER_SECRET", "fixture-secret")
    monkeypatch.setattr(cloud_worker, "AccountRuntime", lambda *a, **kw: runtime)
    monkeypatch.setattr(cloud_worker, "create_app", lambda settings, service: child)
    app = cloud_worker.build_worker()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://worker") as client:
        headers_a = {"X-Account": "account-a", "X-Worker-Secret": "fixture-secret"}
        headers_b = {"X-Account": "account-b", "X-Worker-Secret": "fixture-secret"}
        pending = asyncio.create_task(client.get("/api/probe", headers=headers_a))
        await asyncio.wait_for(started.wait(), 2)
        first = runtime._entries["account-a"].service
        refused = await client.get("/api/probe", headers=headers_b)
        assert refused.status_code == 503
        assert not first.stopped
        release.set()
        assert (await pending).status_code == 200
        assert (await client.get("/api/probe", headers=headers_b)).status_code == 200


@pytest.mark.asyncio
async def test_stopping_service_invalidates_cached_runtime_app(tmp_path):
    runtime = AccountRuntime(Settings(), tmp_path, limit=1)

    async def build(account, key=""):
        return FakeService()

    runtime._build = build
    cache = AppCache(lambda service: object())
    runtime.on_stop(cache.drop)
    first = await runtime.service("account-a")
    old_app = cache.app_for("account-a", first)
    await runtime.service("account-b")
    assert "account-a" not in cache._apps
    second = await runtime.service("account-a")
    assert second is not first
    assert cache.app_for("account-a", second) is not old_app


@pytest.mark.asyncio
async def test_websocket_lease_protects_its_account_until_disconnect(monkeypatch, tmp_path):
    runtime = AccountRuntime(Settings(), tmp_path, limit=1)
    entered, release = asyncio.Event(), asyncio.Event()

    class SocketService(FakeService):
        def __init__(self):
            super().__init__()
            self.bus = EventBus()
            self.phone = SimpleNamespace(detach=lambda _connection: None)

        def state(self):
            return {}

    async def build(account, key=""):
        return SocketService()

    class Socket:
        headers = {"x-account": "account-a", "x-worker-secret": "fixture-secret"}
        accepted = False

        async def accept(self):
            self.accepted = True

        async def send_json(self, message):
            pass

        async def receive_json(self):
            entered.set()
            await release.wait()
            raise WebSocketDisconnect()

        async def close(self, code):
            self.code = code

    runtime._build = build
    monkeypatch.setattr(cloud_worker, "WORKER_SECRET", "fixture-secret")
    monkeypatch.setattr(cloud_worker, "AccountRuntime", lambda *a, **kw: runtime)
    app = cloud_worker.build_worker()
    endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/ws")
    socket = Socket()
    task = asyncio.create_task(endpoint(socket))
    await asyncio.wait_for(entered.wait(), 2)
    assert socket.accepted
    first = runtime._entries["account-a"].service
    with pytest.raises(CapacityError):
        await runtime.service("account-b")
    assert not first.stopped
    release.set()
    await asyncio.wait_for(task, 2)
    assert await runtime.service("account-b")


@pytest.mark.asyncio
async def test_pending_websocket_handshake_is_already_protected(monkeypatch, tmp_path):
    runtime = AccountRuntime(Settings(), tmp_path, limit=1)
    entered, release = asyncio.Event(), asyncio.Event()

    async def build(account, key=""):
        return FakeService()

    class PendingSocket:
        headers = {"x-account": "account-a", "x-worker-secret": "fixture-secret"}

        async def accept(self):
            entered.set()
            await release.wait()
            raise WebSocketDisconnect()

        async def close(self, code):
            self.code = code

    runtime._build = build
    monkeypatch.setattr(cloud_worker, "WORKER_SECRET", "fixture-secret")
    monkeypatch.setattr(cloud_worker, "AccountRuntime", lambda *a, **kw: runtime)
    app = cloud_worker.build_worker()
    endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/ws")
    task = asyncio.create_task(endpoint(PendingSocket()))
    await asyncio.wait_for(entered.wait(), 2)
    first = runtime._entries["account-a"].service
    with pytest.raises(CapacityError):
        await runtime.service("account-b")
    assert not first.stopped
    release.set()
    try:
        await task
    except WebSocketDisconnect:
        pass
    assert isinstance(await runtime.service("account-b"), FakeService)


@pytest.mark.asyncio
async def test_cancelled_build_releases_reserved_capacity(tmp_path):
    runtime = AccountRuntime(Settings(), tmp_path, limit=1)
    entered = asyncio.Event()

    async def build(account, key=""):
        if account == "cancelled":
            entered.set()
            await asyncio.Event().wait()
        return FakeService()

    runtime._build = build
    task = asyncio.create_task(runtime.service("cancelled"))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(100):
        if runtime._building == 0:
            break
        await asyncio.sleep(0.01)
    assert runtime._building == 0
    assert isinstance(await runtime.service("next"), FakeService)
