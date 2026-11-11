"""Queued messenger turns must hydrate causally, never guessing an answer's owner."""

from __future__ import annotations

import asyncio
from functools import partial
from importlib.util import module_from_spec, spec_from_file_location
from itertools import permutations
from pathlib import Path

import pytest

from nanomuse.cloud import CloudError
from nanomuse.schema import LLMResponse, Message
from nanomuse.server.service import MuseService  # noqa: F401 - initialize server before sync
from nanomuse.sync.engine import _strict_history_before_turn

# The relay suite also has a module named test_sync; load our local fixture by path.
_spec = spec_from_file_location("strict_history_sync_fixture", Path(__file__).with_name("test_sync.py"))
assert _spec is not None and _spec.loader is not None
_fixture_module = module_from_spec(_spec)
_spec.loader.exec_module(_fixture_module)
synced = _fixture_module.synced


def test_queued_turns_see_prior_answer_and_skip_future_users(synced, monkeypatch):
    client, service, model, relay = synced
    side = client.post("/api/threads", json={"title": "Ordered turns"}).json()
    thread = service.threads[side["id"]]
    client.portal.call(service.sync.push)
    cid = service.sync.cid_of(thread.id)
    assert cid
    steps = []

    async def run_queue():
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        ask = model.ask

        async def gated_ask(*args, **kwargs):
            if not model.calls:
                first_started.set()
                await release_first.wait()
            return await ask(*args, **kwargs)

        async def take(_thread):
            steps.append("lease")
            return cid, "turn"

        monkeypatch.setattr(model, "ask", gated_ask)
        monkeypatch.setattr(service, "_lease_turn_strict", take)
        monkeypatch.setattr(service.sync, "drop_turn_lease", lambda *_args: None)
        monkeypatch.setattr(service, "cloud_once", lambda *_args, **_kwargs: (model, "mock"))
        model.script[:] = [LLMResponse(content="answer A"), LLMResponse(content="answer B")]
        first = service.send(thread.id, "question A", via="cloud", require_sync_lease=True)
        await asyncio.wait_for(first_started.wait(), 5)
        second = service.send(thread.id, "question B", via="cloud", require_sync_lease=True)
        # A still runs, but B is published ahead of A's assistant response.
        await service.sync.push()
        relay.add_message(cid, "user", "question C")
        release_first.set()
        await asyncio.wait_for(thread.worker, 10)
        return first, second

    first, second = client.portal.call(run_queue)
    assert len(model.calls) == 2
    context = [m.content for m in model.calls[1]["messages"] if m.role in {"user", "assistant"}]
    assert context[-3:] == ["question A", "answer A", "question B"]
    assert "question C" not in context
    rows = sorted(relay.msgs.values(), key=lambda row: row["seq"])
    positions = {row["mid"]: row["seq"] for row in rows}
    answer = next(row for row in rows if row["role"] == "assistant" and row["text"] == "answer A")
    assert positions[first["mid"]] < positions[second["mid"]] < answer["seq"]
    assert steps == ["lease", "lease"]


def test_three_queued_turns_keep_each_answer_next_to_its_question(synced, monkeypatch):
    client, service, model, relay = synced
    side = client.post("/api/threads", json={"title": "Three turns"}).json()
    thread = service.threads[side["id"]]
    client.portal.call(service.sync.push)
    cid = service.sync.cid_of(thread.id)
    assert cid

    async def run_queue():
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        ask = model.ask

        async def gated_ask(*args, **kwargs):
            if not model.calls:
                first_started.set()
                await release_first.wait()
            return await ask(*args, **kwargs)

        async def take(_thread):
            return cid, "turn"

        monkeypatch.setattr(model, "ask", gated_ask)
        monkeypatch.setattr(service, "_lease_turn_strict", take)
        monkeypatch.setattr(service.sync, "drop_turn_lease", lambda *_args: None)
        monkeypatch.setattr(service, "cloud_once", lambda *_args, **_kwargs: (model, "mock"))
        model.script[:] = [LLMResponse(content=f"answer {letter}") for letter in "ABC"]
        service.send(thread.id, "question A", via="cloud", require_sync_lease=True)
        await asyncio.wait_for(first_started.wait(), 5)
        service.send(thread.id, "question B", via="cloud", require_sync_lease=True)
        service.send(thread.id, "question C", via="cloud", require_sync_lease=True)
        await service.sync.push()
        release_first.set()
        await asyncio.wait_for(thread.worker, 10)

    client.portal.call(run_queue)
    assert len(model.calls) == 3, [
        (e.get("type"), e.get("code"), e.get("text"), e.get("reply_to_event"))
        for e in thread.timeline.events
    ]
    context = [m.content for m in model.calls[2]["messages"] if m.role in {"user", "assistant"}]
    assert context[-5:] == ["question A", "answer A", "question B", "answer B", "question C"]
    rows = sorted(relay.msgs.values(), key=lambda row: row["seq"])
    assert [row["text"] for row in rows][:5] == [
        "question A", "question B", "question C", "answer A", "answer B",
    ]


def test_unknown_remote_reply_fails_closed_instead_of_guessing(synced):
    client, service, _model, relay = synced
    side = client.post("/api/threads", json={"title": "Remote answer"}).json()
    thread = service.threads[side["id"]]
    client.portal.call(service.sync.push)
    cid = service.sync.cid_of(thread.id)
    assert cid
    relay.add_message(cid, "user", "question A")
    current_mid = relay.add_message(cid, "user", "question B")
    relay.add_message(cid, "assistant", "an unlinked remote answer")
    with pytest.raises(CloudError) as exc:
        client.portal.call(partial(service.sync.hydrate_thread, thread, cid, current_user_mid=current_mid))
    assert exc.value.code == "sync_order_unknown"


def test_link_from_other_device_restores_the_correct_reply(synced):
    client, service, _model, relay = synced
    side = client.post("/api/threads", json={"title": "Cross-device"}).json()
    thread = service.threads[side["id"]]
    client.portal.call(service.sync.push)
    cid = service.sync.cid_of(thread.id)
    assert cid
    first = relay.add_message(cid, "user", "question A")
    current_mid = relay.add_message(cid, "user", "question B")
    reply = relay.add_message(cid, "assistant", "answer A", device="phone-1")
    relay.msgs[reply]["reply_to_mid"] = first
    client.portal.call(partial(service.sync.hydrate_thread, thread, cid, current_user_mid=current_mid))
    assert [m.content for m in thread.agent.messages] == ["question A", "answer A"]
    assert thread.agent.messages[-1].meta["reply_to_mid"] == first


def test_new_reply_is_published_with_its_original_user_mid(synced):
    client, service, _model, relay = synced
    side = client.post("/api/threads", json={"title": "Link sent"}).json()
    thread = service.threads[side["id"]]
    client.portal.call(service.sync.push)
    user = thread.timeline.add({"type": "user", "text": "question"})
    answer = thread.timeline.add({"type": "assistant", "text": "answer", "final": True, "reply_to_event": user["id"]})
    client.portal.call(service.sync.push)
    wire = next(m for push in relay.pushes for m in push["messages"] if m["mid"] == answer["mid"])
    assert wire["reply_to_mid"] == user["mid"]


def _message(role: str, mid: str) -> Message:
    message = Message.user(mid) if role == "user" else Message.assistant(mid)
    message.meta["mid"] = mid
    return message


def test_multiple_prior_answers_go_with_their_proven_turns():
    history = [_message(role, mid) for role, mid in [
        ("user", "a"), ("user", "b"), ("user", "current"), ("user", "future"),
        ("assistant", "reply-a"), ("assistant", "reply-b"),
    ]]
    events = [
        {"type": "user", "id": "event-a", "mid": "a"},
        {"type": "user", "id": "event-b", "mid": "b"},
        {"type": "assistant", "mid": "reply-a", "reply_to_event": "event-a"},
        {"type": "assistant", "mid": "reply-b", "reply_to_event": "event-b"},
    ]
    result = _strict_history_before_turn(history, 2, events)
    assert [m.content for m in result] == ["a", "reply-a", "b", "reply-b"]


def test_proven_replies_are_reordered_even_when_one_is_before_current_message():
    history = [_message(role, mid) for role, mid in [
        ("user", "a"), ("user", "b"), ("assistant", "reply-a"),
        ("user", "current"), ("assistant", "reply-b"),
    ]]
    history[2].meta["reply_to_mid"] = "a"
    history[4].meta["reply_to_mid"] = "b"
    result = _strict_history_before_turn(history, 3, [])
    assert [m.content for m in result] == ["a", "reply-a", "b", "reply-b"]


def test_every_possible_interleaving_of_two_prior_turns_keeps_causal_order():
    for order in permutations(("a", "b", "current", "reply-a", "reply-b")):
        indexes = {mid: order.index(mid) for mid in order}
        if not (indexes["a"] < indexes["b"] < indexes["current"]
                and indexes["a"] < indexes["reply-a"] and indexes["b"] < indexes["reply-b"]):
            continue
        history = [_message("assistant" if mid.startswith("reply-") else "user", mid) for mid in order]
        history[indexes["reply-a"]].meta["reply_to_mid"] = "a"
        history[indexes["reply-b"]].meta["reply_to_mid"] = "b"
        result = _strict_history_before_turn(history, indexes["current"], [])
        assert [m.content for m in result] == ["a", "reply-a", "b", "reply-b"], order


def test_proven_reply_cannot_cross_an_unlinked_legacy_reply():
    history = [_message(role, mid) for role, mid in [
        ("user", "a"), ("user", "b"), ("assistant", "legacy"),
        ("user", "current"), ("assistant", "reply-a"),
    ]]
    history[4].meta["reply_to_mid"] = "a"
    with pytest.raises(CloudError) as exc:
        _strict_history_before_turn(history, 3, [])
    assert exc.value.code == "sync_order_unknown"


def test_unlinked_legacy_reply_stays_in_place_when_no_move_crosses_it():
    history = [_message(role, mid) for role, mid in [
        ("user", "a"), ("assistant", "legacy"), ("user", "b"),
        ("user", "current"), ("assistant", "reply-b"),
    ]]
    history[4].meta["reply_to_mid"] = "b"
    result = _strict_history_before_turn(history, 3, [])
    assert [m.content for m in result] == ["a", "legacy", "b", "reply-b"]


def test_current_turn_already_answered_before_its_own_row_is_refused():
    history = [_message(role, mid) for role, mid in [
        ("user", "a"), ("assistant", "reply-current"), ("user", "current"),
    ]]
    history[1].meta["reply_to_mid"] = "current"
    with pytest.raises(CloudError) as exc:
        _strict_history_before_turn(history, 2, [])
    assert exc.value.code == "sync_turn_completed"


@pytest.mark.parametrize("parent,code", [("current", "sync_turn_completed"), ("future", "sync_order_unknown")])
def test_reply_to_current_or_future_turn_is_not_ignored(parent, code):
    history = [_message(role, mid) for role, mid in [
        ("user", "a"), ("user", "current"), ("user", "future"), ("assistant", "reply"),
    ]]
    events = [
        {"type": "user", "id": "event-parent", "mid": parent},
        {"type": "assistant", "mid": "reply", "reply_to_event": "event-parent"},
    ]
    with pytest.raises(CloudError) as exc:
        _strict_history_before_turn(history, 1, events)
    assert exc.value.code == code


def test_ambiguous_local_parent_never_falls_back_to_text():
    history = [_message("user", "a"), _message("user", "current"), _message("assistant", "reply")]
    events = [
        {"type": "user", "id": "duplicate", "mid": "a"},
        {"type": "user", "id": "duplicate", "mid": "current"},
        {"type": "assistant", "mid": "reply", "reply_to_event": "duplicate"},
    ]
    with pytest.raises(CloudError) as exc:
        _strict_history_before_turn(history, 1, events)
    assert exc.value.code == "sync_order_unknown"
