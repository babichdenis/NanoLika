"""A shared bot must route connected computers only to their verified owner."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from test_channels_manager import dm, pair, wait_for

from nanomuse.agent import MuseAgent
from nanomuse.config import SentinelSettings, Settings
from nanomuse.llm import MockLLM
from nanomuse.memory import MemoryStore, MemoryStoreRouter, memory_scope
from nanomuse.schema import Function, ToolCall
from nanomuse.sentinel import AuditLog, Sentinel
from nanomuse.tools.base import ToolCollection
from nanomuse.tools.devices import DeviceFiles, Devices
from nanomuse.ui import HeadlessUI

pytest_plugins = ("test_channels_manager",)


@pytest.mark.asyncio
async def test_shared_hub_device_tool_and_prompt_require_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    router = MemoryStoreRouter(MemoryStore(tmp_path / "memory.db"), tmp_path / "scoped")
    calls = []

    class Hub:
        device_name = "owner-bot"
        client = SimpleNamespace(connected=SimpleNamespace(is_set=lambda: True))
        svc = SimpleNamespace(sync=SimpleNamespace(account_id="account-owner"), app=SimpleNamespace(memory=router))

        def others(self):
            return [{"id": "owner-pc", "name": "owner-computer", "online": True, "kind": "computer"}]

        def find(self, _):
            return self.others()[0]

        async def call(self, *_args, **_kwargs):
            calls.append(True)
            return {"path": "~", "entries": [{"name": "owner-private-marker.txt"}]}

    hub = Hub()
    ui = HeadlessUI(approve=False)
    gate = Sentinel(SentinelSettings(), AuditLog(tmp_path / "audit"), ui)
    gate.channel_mode = lambda _: "full-access"
    files = DeviceFiles(hub=hub)
    agent = MuseAgent(Settings(), MockLLM([]), ToolCollection(Devices(hub=hub)), gate, ui, gate.audit)
    call = ToolCall(function=Function(name="device_files", arguments=json.dumps({"device": "owner-computer"})))

    with memory_scope("account:attacker"):
        token = gate.begin_task(conversation="other-customer")
        try:
            result = await gate.guard(call, files)
            assert not result.ok and not calls
            assert "owner-computer" not in agent.devices_section()
        finally:
            gate.end_task(token)
    with memory_scope("account:account-owner"):
        token = gate.begin_task(conversation="owner")
        try:
            result = await gate.guard(call, files)
            assert result.ok and "owner-private-marker" in result.output
            assert "owner-computer" in agent.devices_section()
        finally:
            gate.end_task(token)
    before = len(calls)
    hub.svc.sync.account_id = ""
    with memory_scope("account:account-owner"):
        assert not (await files.execute(device="owner-computer")).ok
    hub.svc.sync.account_id = "account-owner"
    hub.svc.app.memory = None
    assert not (await files.execute(device="owner-computer")).ok
    assert len(calls) == before
    router.close()


@pytest.mark.parametrize("identity", ["other", "missing", "expired", "revoked", "owner"])
def test_shared_computer_command_requires_same_verified_account(chan, monkeypatch, identity):
    client, service, _llm, manager, fake = chan
    pair(client, fake)
    service.sync.state["account_id"] = "owner"
    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    device = {"id": "fixture-pc", "online": True, "kind": "computer", "actions": ["task"]}
    monkeypatch.setattr(service.hub, "others", lambda: [device])
    received = []

    async def handoff(*args, **kwargs):
        received.append(args)

    async def authenticate(channel, msg):
        if identity == "expired":
            await manager._send(msg.channel, msg.chat_id, "authentication-failed")
            return "!", ""
        if identity == "missing":
            return "", ""
        manager._cloud_customer_scopes[(channel, msg.sender_id)] = "account:" + ("owner" if identity == "revoked" else identity)
        return "verified-fixture-key", "model"

    async def refuse(msg, **kwargs):
        await manager._send(msg.channel, msg.chat_id, "wrong-account-refused")

    async def confirmed(_msg, _key):
        return identity == "owner"

    async def no_account(channel, chat):
        await manager._send(channel, chat, "authentication-failed")

    monkeypatch.setattr(manager, "_serve_from_device", handoff)
    monkeypatch.setattr(manager, "_cloud_key_for", authenticate)
    monkeypatch.setattr(manager, "_cloud_no_account", no_account)
    monkeypatch.setattr(manager.menu, "cloud_handoff", refuse)
    monkeypatch.setattr(manager, "_shared_sender_hub_confirmed", confirmed)
    fake.bus.inbound(dm("/computer open owner file"))
    wait_for(lambda: bool(received) or "wrong-account-refused" in fake.texts() or "authentication-failed" in fake.texts())
    if identity == "owner":
        assert received and received[0][3] == device
    else:
        assert not received
        assert ("authentication-failed" if identity in {"expired", "missing"} else "wrong-account-refused") in fake.texts()


@pytest.mark.parametrize("identity", ["other", "owner"])
def test_shared_normal_turn_adds_hub_context_only_for_verified_owner(chan, monkeypatch, identity):
    client, service, _llm, manager, fake = chan
    pair(client, fake)
    service.sync.state["account_id"] = "owner"
    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    context_calls = []
    sent = []

    async def authenticate(channel, msg):
        manager._cloud_customer_scopes[(channel, msg.sender_id)] = "account:" + identity
        return "verified-fixture-key", "model"

    monkeypatch.setattr(manager, "_cloud_key_for", authenticate)
    monkeypatch.setattr(manager, "_shared_runtime_context", lambda thread: context_calls.append(thread))
    monkeypatch.setattr(service, "send", lambda *args, **kwargs: sent.append((args, kwargs)))
    fake.bus.inbound(dm("Tell me something new"))
    wait_for(lambda: sent)
    assert bool(context_calls) == (identity == "owner")


def test_selected_foreign_conversation_is_rejected_before_routing_or_hydration(chan, monkeypatch):
    client, service, _llm, manager, fake = chan
    pair(client, fake)
    service.sync.state["account_id"] = "owner"
    service.timeline("victim-thread")
    victim = service.threads["victim-thread"]
    victim.customer_scope = "account:owner"
    service.sync.state["owners"]["victim-thread"] = "owner"
    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    monkeypatch.setattr(manager.picker, "selected", lambda *_args: "victim-thread")
    hydrated, echoed, sent = [], [], []

    async def authenticate(channel, msg):
        manager._cloud_customer_scopes[(channel, msg.sender_id)] = "account:attacker"
        return "valid-attacker-key", "model"

    async def hydrate(*args):
        hydrated.append(args)
        return True

    async def echo(*args):
        echoed.append(args)

    monkeypatch.setattr(manager, "_cloud_key_for", authenticate)
    monkeypatch.setattr(manager, "_hydrate_shared_thread", hydrate)
    monkeypatch.setattr(manager, "_echo_selected_user", echo)
    monkeypatch.setattr(service, "send", lambda *args, **kwargs: sent.append((args, kwargs)))
    before = manager.routes.get("victim-thread")
    fake.bus.inbound(dm("Tell me what we were discussing"))
    wait_for(lambda: any("could not confirm" in text for text in fake.texts()))
    assert not hydrated and not echoed and not sent
    assert manager.routes.get("victim-thread") == before


@pytest.mark.parametrize("valid", [False, True])
def test_selected_owner_conversation_requires_live_owner_key_before_sync(chan, monkeypatch, valid):
    client, service, _llm, manager, fake = chan
    pair(client, fake)
    service.sync.state["account_id"] = "owner"
    service.timeline("owner-thread")
    service.threads["owner-thread"].customer_scope = "account:owner"
    service.sync.state["owners"]["owner-thread"] = "owner"
    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    monkeypatch.setattr(manager.picker, "selected", lambda *_args: "owner-thread")
    hydrated, sent = [], []

    async def authenticate(channel, msg):
        manager._cloud_customer_scopes[(channel, msg.sender_id)] = "account:owner"
        return "owner-key", "model"

    async def confirmed(_msg, _key, _scope):
        return valid

    async def hydrate(*args):
        hydrated.append(args)
        return True

    async def echo(*_args):
        pass

    async def no_account(channel, chat):
        await manager._send(channel, chat, "auth-refused")

    monkeypatch.setattr(manager, "_cloud_key_for", authenticate)
    monkeypatch.setattr(manager, "_shared_sender_account_confirmed", confirmed)
    monkeypatch.setattr(manager, "_cloud_no_account", no_account)
    monkeypatch.setattr(manager, "_hydrate_shared_thread", hydrate)
    monkeypatch.setattr(manager, "_echo_selected_user", echo)
    monkeypatch.setattr(manager, "_shared_runtime_context", lambda _thread: None)
    monkeypatch.setattr(service, "send", lambda *args, **kwargs: sent.append((args, kwargs)))
    fake.bus.inbound(dm("Continue our work"))
    wait_for(lambda: bool(sent) or "auth-refused" in fake.texts())
    assert bool(hydrated) == valid and bool(sent) == valid


@pytest.mark.parametrize("outcome, expected", [("owner", True), ("other", False), ("revoked", False)])
def test_hub_owner_revalidation_uses_live_account_even_with_sync_off(chan, monkeypatch, outcome, expected):
    client, service, _llm, manager, _fake = chan
    service.sync.state["account_id"] = "owner"
    service.sync.state["enabled"] = False
    manager._cloud_customer_scopes[("fake", "u1")] = "account:owner"
    monkeypatch.setattr(manager.store, "resolved", lambda _cfg: {"cloud_url": "https://cloud.example.test"})
    closed = []

    class FakeCloud:
        def __init__(self, **kwargs):
            assert kwargs["api_key"] == "fixture-key"

        async def me(self):
            if outcome == "revoked":
                raise RuntimeError("revoked")
            return {"account": {"id": outcome}}

        async def close(self):
            closed.append(True)

    monkeypatch.setattr("nanomuse.cloud.CloudClient", FakeCloud)
    assert client.portal.call(manager._shared_sender_hub_confirmed, dm("x"), "fixture-key") is expected
    assert closed


def test_other_account_identity_is_confirmed_without_borrowing_hub(chan, monkeypatch):
    client, service, _llm, manager, _fake = chan
    service.sync.state["account_id"] = "owner-a"
    manager._cloud_customer_scopes[("fake", "u1")] = "account:customer-b"
    monkeypatch.setattr(manager.store, "resolved", lambda _cfg: {"cloud_url": "https://cloud.example.test"})

    class FakeCloud:
        def __init__(self, **_kwargs):
            pass

        async def me(self):
            return {"account": {"id": "customer-b"}}

        async def close(self):
            pass

    monkeypatch.setattr("nanomuse.cloud.CloudClient", FakeCloud)
    assert client.portal.call(manager._shared_sender_account_confirmed, dm("x"), "customer-b-key", "account:customer-b")
    assert not client.portal.call(manager._shared_sender_hub_confirmed, dm("x"), "customer-b-key")


@pytest.mark.parametrize("per_chat", ["true", "false"])
def test_shared_pinned_owner_still_signs_in_and_proves_account(chan, monkeypatch, per_chat):
    import httpx

    from nanomuse.channels.base import InboundMessage

    client, service, _llm, manager, _fake = chan
    service.sync.state["account_id"] = "owner"
    service.app.vault.set("NANOMUSE_CLOUD_KEY", "installation-key")
    monkeypatch.setattr(manager.pairing, "approved", lambda _channel: {"123": {"runtime_account": "owner"}})
    monkeypatch.setattr(manager.store, "resolved", lambda _cfg: {
        "cloud_per_chat": per_chat, "cloud_url": "https://cloud.example.test",
        "cloud_bot_token": "fixture-secret", "public": "true",
    })
    requests = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def post(self, *_args, **_kwargs):
            requests.append(True)
            return httpx.Response(200, json={"api_key": "owner-key", "models": [], "account": {"id": "owner"}})

    monkeypatch.setattr("nanomuse.channels.manager.httpx.AsyncClient", FakeClient)
    msg = InboundMessage(channel="telegram", chat_id="123", sender_id="123", text="hello")
    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    assert client.portal.call(manager._cloud_key_for, "telegram", msg) == ("owner-key", "")
    assert requests and manager._cloud_customer_scopes[("telegram", "123")] == "account:owner"

    monkeypatch.delenv("NANOMUSE_SHARED_CLOUD_BOT", raising=False)
    requests.clear()
    assert client.portal.call(manager._cloud_key_for, "telegram", msg) == ("", "")
    assert not requests


def test_other_account_can_continue_its_own_selected_local_conversation(chan, monkeypatch):
    from nanomuse.schema import LLMResponse

    client, service, llm, manager, fake = chan
    pair(client, fake)
    service.sync.state["account_id"] = "owner-a"
    service.timeline("customer-b-thread")
    thread = service.threads["customer-b-thread"]
    thread.customer_scope = "account:customer-b"
    thread.workspace_scope = "account:customer-b"
    service.sync.state["owners"]["customer-b-thread"] = "customer-b"
    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    monkeypatch.setattr(manager.picker, "selected", lambda *_args: "customer-b-thread")

    async def authenticate(channel, msg):
        manager._cloud_customer_scopes[(channel, msg.sender_id)] = "account:customer-b"
        return "customer-b-key", "model"

    async def confirmed(_msg, key, scope):
        return key == "customer-b-key" and scope == "account:customer-b"

    monkeypatch.setattr(manager, "_cloud_key_for", authenticate)
    monkeypatch.setattr(manager, "_shared_sender_account_confirmed", confirmed)
    monkeypatch.setattr(service, "cloud_once", lambda *_args: (llm, "model"))
    llm.script.extend([LLMResponse(content="first reply"), LLMResponse(content="second reply")])
    fake.bus.inbound(dm("first request"))
    wait_for(lambda: any("first reply" in t for t in fake.texts()))
    fake.bus.inbound(dm("second request"))
    wait_for(lambda: any("second reply" in t for t in fake.texts()))
    users = [event.get("text") for event in thread.timeline.events if event.get("type") == "user"]
    assert users[-2:] == ["first request", "second request"]
    assert "first request" in str(llm.calls[-1])
