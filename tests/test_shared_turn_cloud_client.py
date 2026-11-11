"""Paid cloud tools must use the authenticated key of their own turn."""

from __future__ import annotations

import asyncio
import json
from contextvars import ContextVar
from types import SimpleNamespace

import pytest

from nanomuse.app import NanoMuseApp
from nanomuse.cloud_context import current_cloud_client, turn_cloud_client
from nanomuse.config import SentinelSettings
from nanomuse.llm import MockLLM
from nanomuse.schema import Function, ToolCall
from nanomuse.sentinel import AuditLog, Sentinel
from nanomuse.server.service import MuseService
from nanomuse.tools.beatapi import BeatAPI
from nanomuse.tools.media import Media
from nanomuse.tools.picture import Picture
from nanomuse.tools.share import ShareFile
from nanomuse.tools.video import Video
from nanomuse.ui import HeadlessUI
from nanomuse.workspace_context import workspace_context


class FakeCloud:
    instances = []

    def __init__(self, base_url, api_key):
        self.base_url = base_url
        self.api_key = api_key
        self.closed = False
        self.instances.append(self)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_concurrent_turns_get_distinct_clients_and_no_shared_fallback(monkeypatch):
    FakeCloud.instances = []
    monkeypatch.setattr("nanomuse.cloud_context.CloudClient", FakeCloud)
    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    app = NanoMuseApp.__new__(NanoMuseApp)
    app.hub = SimpleNamespace(cloud=SimpleNamespace(api_key="installation-key"))
    assert app._cloud_client() is None
    ready = asyncio.Barrier(2)

    async def task(key):
        async with turn_cloud_client("https://cloud.example.test", key) as client:
            await ready.wait()
            assert current_cloud_client() is client is app._cloud_client()
            await asyncio.sleep(0)
            return client.api_key

    assert await asyncio.gather(task("account-a-key"), task("account-b-key")) == ["account-a-key", "account-b-key"]
    assert all(client.closed for client in FakeCloud.instances)
    assert current_cloud_client() is None and app._cloud_client() is None
    monkeypatch.delenv("NANOMUSE_SHARED_CLOUD_BOT", raising=False)
    assert app._cloud_client().api_key == "installation-key"


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
async def test_turn_client_is_closed_and_context_cleared_on_error(monkeypatch, error):
    FakeCloud.instances = []
    monkeypatch.setattr("nanomuse.cloud_context.CloudClient", FakeCloud)
    with pytest.raises(error):
        async with turn_cloud_client("https://cloud.example.test", "account-key"):
            raise error()
    assert FakeCloud.instances[-1].closed
    assert current_cloud_client() is None


@pytest.mark.asyncio
async def test_real_service_worker_passes_each_turns_key_without_persisting(settings, monkeypatch):
    FakeCloud.instances = []
    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    monkeypatch.setattr("nanomuse.cloud_context.CloudClient", FakeCloud)
    model = MockLLM([])
    service = MuseService(settings, llm=model)
    monkeypatch.setattr(service, "cloud_once", lambda *_args, **_kwargs: (model, "fixture-model"))
    service.timeline("customer-a")
    service.timeline("customer-b")
    ready = asyncio.Barrier(2)
    seen = {}

    def make_run(label):
        async def run(*_args, **_kwargs):
            await ready.wait()
            client = service.app._cloud_client()
            seen[label] = client.api_key if client else None
            await asyncio.sleep(0)
            return "done"
        return run

    a, b = service.threads["customer-a"], service.threads["customer-b"]
    monkeypatch.setattr(a.agent, "run", make_run("a"))
    monkeypatch.setattr(b.agent, "run", make_run("b"))
    service.send("customer-a", "first", once_key="account-a-key", once_base="https://cloud-a.test", customer_scope="account:a")
    service.send("customer-b", "second", once_key="account-b-key", once_base="https://cloud-b.test", customer_scope="account:b")
    await asyncio.gather(a.worker, b.worker)
    assert seen == {"a": "account-a-key", "b": "account-b-key"}
    assert {instance.base_url for instance in FakeCloud.instances} == {"https://cloud-a.test", "https://cloud-b.test"}
    assert len(FakeCloud.instances) == 2 and all(instance.closed for instance in FakeCloud.instances)
    assert "account-a-key" not in str(a.timeline.events)
    assert "account-b-key" not in str(b.timeline.events)
    assert service.app._cloud_client() is None


def test_explicit_chat_key_wins_over_installation_key(settings, monkeypatch):
    service = MuseService(settings, llm=MockLLM([]))
    service.app.vault.set("NANOMUSE_CLOUD_KEY", "installation-key")
    chosen = []
    monkeypatch.setattr("nanomuse.server.service.create_llm", lambda llm, **_kwargs: chosen.append(llm.api_key))
    service.cloud_once("customer-key", "fixture-model")
    assert chosen == ["customer-key"]


@pytest.mark.asyncio
async def test_real_paid_tools_use_only_their_own_clients_in_parallel(tmp_path, monkeypatch):
    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    expected = ContextVar("expected_test_account")
    events = []

    class Relay(FakeCloud):
        def record(self, name):
            assert self.api_key == expected.get()
            events.append((self.api_key, name))

        async def image(self, *_args, **_kwargs):
            self.record("picture")
            return b"fixture-png", {}

        async def video(self, *_args, **_kwargs):
            self.record("video")
            return b"fixture-video", {}

        async def models(self):
            self.record("models")
            return [{"id": "speech-model", "nanomuse": {"audio_kind": "speech"}}]

        async def speech(self, *_args, **_kwargs):
            self.record("speech")
            return b"fixture-audio", "audio/mpeg", {}

        async def upload(self, *_args, **_kwargs):
            self.record("upload")
            return "https://files.example.test/result"

        async def _request(self, *_args, **_kwargs):
            self.record("beatapi")
            return {"ok": True}

    monkeypatch.setattr("nanomuse.cloud_context.CloudClient", Relay)
    app = NanoMuseApp.__new__(NanoMuseApp)
    app.hub = SimpleNamespace(cloud=SimpleNamespace(api_key="installation-key"))
    gate = Sentinel(SentinelSettings(), AuditLog(tmp_path / "audit"), HeadlessUI(approve=True))
    barrier = asyncio.Barrier(2)

    async def turn(key):
        session = tmp_path / key
        session.mkdir()
        (session / "document.txt").write_text("personal document")
        token = expected.set(key)
        sentinel_token = gate.begin_task(conversation=key)
        try:
            async with turn_cloud_client("https://cloud.example.test", key):
                with workspace_context(key, session):
                    await barrier.wait()
                    cases = [
                        (Picture(workspace=session, cloud=app._cloud_client), {"prompt": "cat", "model": "picture"}),
                        (Video(workspace=session, cloud=app._cloud_client), {"prompt": "cat", "model": "video"}),
                        (Media(workspace=session, cloud=app._cloud_client), {"action": "speech", "text": "hello"}),
                        (BeatAPI(cloud=app._cloud_client), {"action": "run", "reference": "fixture"}),
                        (ShareFile(workspace=session, cloud=app._cloud_client), {"path": "document.txt"}),
                    ]
                    for tool, args in cases:
                        call = ToolCall(function=Function(name=tool.name, arguments=json.dumps(args)))
                        result = await gate.guard(call, tool)
                        assert result.ok, result.error
                        assert key not in result.for_model()
        finally:
            gate.end_task(sentinel_token)
            expected.reset(token)

    await asyncio.gather(turn("account-a-key"), turn("account-b-key"))
    for key in ("account-a-key", "account-b-key"):
        assert {name for event_key, name in events if event_key == key} >= {
            "picture", "video", "speech", "beatapi", "upload", "models",
        }
    assert all(event_key != "installation-key" for event_key, _ in events)
    assert "account-a-key" not in (tmp_path / "audit").read_text()
    assert "account-b-key" not in (tmp_path / "audit").read_text()


@pytest.mark.asyncio
async def test_unverified_turn_never_receives_a_paid_client(settings, monkeypatch):
    FakeCloud.instances = []
    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    monkeypatch.setattr("nanomuse.cloud_context.CloudClient", FakeCloud)
    model = MockLLM([])
    service = MuseService(settings, llm=model)
    monkeypatch.setattr(service, "cloud_once", lambda *_args, **_kwargs: (model, "fixture-model"))
    service.timeline("unverified")
    thread = service.threads["unverified"]
    seen = []

    async def run(*_args, **_kwargs):
        seen.append(service.app._cloud_client())
        return "done"

    monkeypatch.setattr(thread.agent, "run", run)
    with pytest.raises(ValueError, match="подтвердить аккаунт"):
        service.send("unverified", "test", once_key="unknown-key", once_base="https://cloud.example.test", customer_scope="channel:unknown")
    assert seen == [] and not FakeCloud.instances


@pytest.mark.asyncio
async def test_turn_client_closes_when_running_task_is_cancelled(monkeypatch):
    FakeCloud.instances = []
    monkeypatch.setattr("nanomuse.cloud_context.CloudClient", FakeCloud)
    ready = asyncio.Event()

    async def work():
        async with turn_cloud_client("https://cloud.example.test", "account-key"):
            ready.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(work())
    await ready.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(FakeCloud.instances) == 1 and FakeCloud.instances[0].closed
    assert current_cloud_client() is None


@pytest.mark.asyncio
async def test_expired_customer_key_cannot_fall_back_to_installation(tmp_path, monkeypatch):
    from nanomuse.cloud import CloudError

    used = []

    class RejectingRelay(FakeCloud):
        async def image(self, *_args, **_kwargs):
            used.append(self.api_key)
            raise CloudError(401, "invalid_key", "expired")

    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    monkeypatch.setattr("nanomuse.cloud_context.CloudClient", RejectingRelay)
    app = NanoMuseApp.__new__(NanoMuseApp)
    app.hub = SimpleNamespace(cloud=SimpleNamespace(api_key="installation-key"))
    gate = Sentinel(SentinelSettings(), AuditLog(tmp_path / "audit"), HeadlessUI(approve=True))
    tool = Picture(workspace=tmp_path, cloud=app._cloud_client)
    call = ToolCall(function=Function(name=tool.name, arguments=json.dumps({"prompt": "cat", "model": "picture"})))
    async with turn_cloud_client("https://cloud.example.test", "expired-customer-key"):
        result = await gate.guard(call, tool)
    assert not result.ok
    assert used == ["expired-customer-key"]
    assert app._cloud_client() is None
