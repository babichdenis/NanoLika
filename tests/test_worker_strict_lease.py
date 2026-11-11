"""Messenger turns cannot start or continue without a proved conversation lease."""

from __future__ import annotations

import asyncio
from unittest.mock import PropertyMock

import pytest

from nanomuse.cloud import CloudError
from nanomuse.llm import MockLLM
from nanomuse.schema import LLMResponse
from nanomuse.server.service import MuseService


@pytest.fixture
def runtime(settings, monkeypatch):
    model = MockLLM([LLMResponse(content="answer")])
    service = MuseService(settings, llm=model)
    service.timeline("chat")
    thread = service.threads["chat"]
    service.sync.state["account_id"] = "customer"
    service.sync.state["owners"]["chat"] = "customer"
    service.sync.state["cids"]["chat"] = "cid"
    monkeypatch.setattr(type(service.sync), "active", PropertyMock(return_value=True))
    monkeypatch.setattr(service.sync, "_eligible", lambda _thread: True)
    monkeypatch.setattr(service.sync, "message_sent", lambda _thread: None)
    monkeypatch.setattr(service.sync, "turn_finished", lambda _thread: None)
    monkeypatch.setattr(service.sync, "drop_turn_lease", lambda *_args: None)
    monkeypatch.setattr(service, "cloud_once", lambda *_args, **_kwargs: (model, "test-model"))
    return service, thread, model


def submit(service):
    return service.send(
        "chat", "same text", via="cloud", once_key="customer-key", once_base="https://relay.example",
        customer_scope="account:customer", user_event_id="e_fixture", user_mid="msg-fixture",
        require_sync_lease=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["busy", "none"])
async def test_busy_or_unavailable_lease_never_runs_model(runtime, monkeypatch, state):
    service, thread, model = runtime

    async def take(*_args, **_kwargs):
        return state, {}

    monkeypatch.setattr(service.sync, "take_turn_lease", take)
    event = submit(service)
    await thread.worker
    assert not model.calls
    failure = next(row for row in thread.timeline.events if row.get("reply_to_event") == event["id"])
    assert failure["level"] == "error" and "Попробуйте позже" in failure["text"]


@pytest.mark.asyncio
async def test_lease_is_taken_before_hydrating_exact_message(runtime, monkeypatch):
    service, thread, model = runtime
    steps = []

    async def take(*_args, **_kwargs):
        steps.append("lease")
        return "held", {}

    async def hydrate(_thread, _cid, *, current_user_mid):
        steps.append(current_user_mid)

    monkeypatch.setattr(service.sync, "take_turn_lease", take)
    monkeypatch.setattr(service.sync, "hydrate_thread", hydrate)
    event = submit(service)
    await thread.worker
    assert steps == ["lease", event["mid"]]
    assert model.calls
    result = next(row for row in thread.timeline.events if row.get("text") == "answer")
    assert result["reply_to_event"] == event["id"]


@pytest.mark.asyncio
async def test_missing_message_releases_lease_and_does_not_run(runtime, monkeypatch):
    service, thread, model = runtime
    released = []

    async def take(*_args, **_kwargs):
        return "held", {}

    async def hydrate(*_args, **_kwargs):
        raise CloudError(409, "sync_message_missing", "message missing")

    monkeypatch.setattr(service.sync, "take_turn_lease", take)
    monkeypatch.setattr(service.sync, "hydrate_thread", hydrate)
    monkeypatch.setattr(service.sync, "drop_turn_lease", lambda cid, turn: released.append((cid, turn)))
    event = submit(service)
    await thread.worker
    assert released and not model.calls
    assert any(row.get("reply_to_event") == event["id"] and row.get("level") == "error" for row in thread.timeline.events)


@pytest.mark.asyncio
async def test_lease_loss_cancels_running_model(runtime, monkeypatch):
    service, thread, _model = runtime
    started = asyncio.Event()
    stopped = asyncio.Event()
    monkeypatch.setattr("nanomuse.server.service.LEASE_RENEW_S", 0.01)

    async def take(*_args, **_kwargs):
        return "held", {}

    async def hydrate(*_args, **_kwargs):
        return None

    async def renew(*_args, **_kwargs):
        await started.wait()
        assert _kwargs.get("strict") is True
        return False

    async def run(*_args, **_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(service.sync, "take_turn_lease", take)
    monkeypatch.setattr(service.sync, "hydrate_thread", hydrate)
    monkeypatch.setattr(service.sync, "renew_turn_lease", renew)
    monkeypatch.setattr(thread.agent, "run", run)
    event = submit(service)
    await asyncio.wait_for(thread.worker, timeout=5)
    assert stopped.is_set()
    assert any(row.get("reply_to_event") == event["id"] and row.get("level") == "error" for row in thread.timeline.events)


def test_strict_preflight_rejects_avatar_and_pending_question_before_event(runtime, monkeypatch):
    from types import SimpleNamespace

    service, thread, model = runtime
    before = list(thread.timeline.events)
    monkeypatch.setattr(service.ui, "has_pending_question", lambda _thread: False)
    with pytest.raises(ValueError, match="аватара"):
        service.send("chat", "new avatar: a robot owl", require_sync_lease=True)
    assert thread.timeline.events == before and not model.calls
    service.avatar.current = SimpleNamespace(thread="chat", stage="choose")
    with pytest.raises(ValueError, match="аватара"):
        service.send("chat", "1", require_sync_lease=True)
    monkeypatch.setattr(service.ui, "has_pending_question", lambda _thread: True)
    with pytest.raises(ValueError, match="ожидающий вопрос"):
        service.send("chat", "answer", require_sync_lease=True)
    assert thread.timeline.events == before and not model.calls
