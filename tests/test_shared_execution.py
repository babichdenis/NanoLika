"""Messenger continuation stays in the same shared chat, with an offline server fallback."""

from types import SimpleNamespace

import pytest

from nanomuse.channels.manager import ChannelManager
from nanomuse.hub.client import HubError


def world(*, online=True, cid="shared", paused=False):
    calls = []
    device = {
        "id": "desktop",
        "name": "Desk",
        "kind": "computer",
        "online": online,
        "actions": ["shared_chat", "task"],
    }
    thread = SimpleNamespace(
        busy=False,
        origin_device="desktop",
        agent=SimpleNamespace(messages=[]),
        timeline=SimpleNamespace(events=[{"type": "assistant", "via_device": "desktop"}]),
    )

    async def call(target, action, args, **kwargs):
        calls.append((target, action, args))
        if paused:
            raise HubError("not_allowed", "sync paused on this device")
        return {"cid": args["cid"], "sessionId": "existing-session"}

    manager = ChannelManager.__new__(ChannelManager)
    manager.svc = SimpleNamespace(
        hub=SimpleNamespace(primary=lambda: None, others=lambda: [device], call=call),
        sync=SimpleNamespace(active=True, cid_of=lambda _: cid, _eligible=lambda _: True),
        threads={"main": thread},
    )
    return manager, thread, device, calls


@pytest.mark.asyncio
async def test_online_continuation_probes_same_conversation_without_starting_a_model():
    manager, _, device, calls = world()
    assert (await manager._shared_execution_device("main"))["id"] == device["id"]
    assert calls == [("desktop", "shared_chat", {"cid": "shared"})]


@pytest.mark.asyncio
async def test_offline_computer_keeps_server_chat_and_reconnect_uses_same_cid():
    manager, thread, device, calls = world(online=False)
    assert await manager._shared_execution_device("main") is None
    assert calls == []
    manager._shared_runtime_context("main")
    note = thread.agent.messages[-1].content
    assert '"online": false' in note
    assert "continue ordinary discussion" in note
    assert "cannot be checked" in note
    assert "never restart a generation" in note
    device["online"] = True
    assert (await manager._shared_execution_device("main"))["id"] == device["id"]
    assert calls[-1][2] == {"cid": "shared"}


@pytest.mark.asyncio
async def test_cloud_paused_on_computer_falls_back_without_starting_a_task():
    manager, _, _, calls = world(paused=True)
    assert await manager._shared_execution_device("main") is None
    assert len(calls) == 1 and calls[0][1] == "shared_chat"


@pytest.mark.asyncio
async def test_unrelated_computer_is_not_chosen_for_a_new_cloud_chat():
    manager, thread, _, calls = world()
    thread.origin_device = "cloud"
    thread.timeline.events = [{"type": "assistant", "via_device": "cloud"}]
    assert await manager._shared_execution_device("main") is None
    assert calls == []


@pytest.mark.asyncio
async def test_existing_desktop_is_probed_when_the_hello_marker_is_missing():
    manager, _, device, calls = world()
    device["actions"] = ["task", "chat.open_desktop"]
    selected = await manager._shared_execution_device("main")
    assert selected["id"] == device["id"]
    assert "shared_chat" in selected["actions"]
    assert calls == [("desktop", "shared_chat", {"cid": "shared"})]


@pytest.mark.asyncio
async def test_successful_catalogue_pull_clears_a_previous_transport_error():
    from unittest.mock import AsyncMock

    from nanomuse.sync.engine import ConversationSync

    engine = SimpleNamespace(
        client=SimpleNamespace(cloud=SimpleNamespace(api_key="")),
        svc=SimpleNamespace(hub=SimpleNamespace(_key=lambda: "fixture-key")),
        catalog=SimpleNamespace(pull=AsyncMock(return_value=0)),
        last_error="http_502",
        last_pull_at=None,
    )
    assert await ConversationSync._pull_locked(engine) == 0
    assert engine.last_error == ""
    assert engine.last_pull_at
