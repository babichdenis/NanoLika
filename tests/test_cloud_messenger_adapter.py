"""Opt-in adapter selects only account chats and sends to the originating topic."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nanomuse.channels.base import InboundMessage
from nanomuse.channels.cloud_messenger_adapter import CloudMessengerAdapter


def catalog(account):
    return {"account_id": account, "complete": True, "conversations": [
        {"cid": "shared-cid", "title": "Active", "archived": False},
        {"cid": "old-cid", "title": "Archived", "archived": True},
    ]}


@pytest.mark.asyncio
async def test_adapter_scopes_selection_and_delivery_to_sender_and_topic(tmp_path, monkeypatch):
    sent = []
    manager = SimpleNamespace(
        svc=SimpleNamespace(data_dir=tmp_path), channels={},
        picker=SimpleNamespace(topics=SimpleNamespace(file=SimpleNamespace(load=lambda: {}))),
    )

    async def reply(channel, chat, text):
        sent.append((channel, chat, text))

    manager._send = reply
    adapter = CloudMessengerAdapter(manager)

    async def identity(msg):
        return ("a" if msg.sender_id == "100" else "b"), "key", "https://relay.test", "token"

    async def get_catalog(account, *_args):
        return catalog(account)

    monkeypatch.setattr(adapter, "_identity", identity)
    monkeypatch.setattr(adapter, "_catalog", get_catalog)
    for sender in ("100", "200"):
        chat = sender + ":topic:1"
        assert await adapter.handle(InboundMessage("telegram", chat, sender, text="/chats"))
        assert await adapter.handle(InboundMessage("telegram", chat, sender, text="/chat 1"))
        assert await adapter.handle(InboundMessage("telegram", chat, sender, text="hello-" + sender, message_id="10"))
    jobs = adapter.bridge.pending()
    assert len(jobs) == 2 and {job["account"] for job in jobs} == {"a", "b"}
    assert {job["conversation"] for job in jobs} == {"shared-cid"}
    assert all("Archived" not in text for _channel, _chat, text in sent)
    assert {job["chat"] for job in jobs} == {"100:topic:1", "200:topic:1"}


@pytest.mark.asyncio
async def test_adapter_refuses_attachment_without_falling_back(tmp_path, monkeypatch):
    sent = []
    manager = SimpleNamespace(svc=SimpleNamespace(data_dir=tmp_path), channels={})

    async def reply(_channel, _chat, text):
        sent.append(text)

    manager._send = reply
    adapter = CloudMessengerAdapter(manager)

    async def identity(_msg):
        return "a", "key", "https://relay.test", "token"

    async def get_catalog(*_args):
        return catalog("a")

    monkeypatch.setattr(adapter, "_identity", identity)
    monkeypatch.setattr(adapter, "_catalog", get_catalog)
    msg = InboundMessage("max", "chat", "user", text="file", message_id="1", files=[object()])
    assert await adapter.handle(msg)
    assert "вложений" in sent[-1] and not adapter.bridge.pending()


@pytest.mark.asyncio
async def test_automatic_topics_imports_known_and_creates_only_new_active(tmp_path, monkeypatch):
    calls = []

    class Telegram:
        topics_enabled = True

        async def _call(self, method, args):
            calls.append((method, args))
            return {"message_thread_id": 99}

    rows = {
        "legacy": {"account": "a", "owner": "100", "chat": "100", "topic": 41, "cid": "known"},
        "retired": {"account": "a", "owner": "100", "chat": "100", "topic": 42, "cid": "retired", "archived": True},
    }
    manager = SimpleNamespace(
        svc=SimpleNamespace(data_dir=tmp_path), channels={"telegram": Telegram()},
        picker=SimpleNamespace(topics=SimpleNamespace(file=SimpleNamespace(load=lambda: rows))),
    )
    adapter = CloudMessengerAdapter(manager)
    monkeypatch.setattr(adapter.topics, "enroll", lambda *_args: 1000.0)
    data = {"account_id": "a", "complete": True, "conversations": [
        {"cid": "known", "title": "Known", "created_at": 900},
        {"cid": "retired", "title": "Retired", "created_at": 1100},
        {"cid": "unmapped-old", "title": "Unmapped", "created_at": 900},
        {"cid": "new", "title": "New", "created_at": 1100},
        {"cid": "archive", "title": "Archive", "created_at": 1100, "archived": True},
    ]}
    msg = InboundMessage("telegram", "100|41", "100", text="hello", message_id="1")
    await adapter._sync_topics("a", msg, data)
    assert [item[1]["name"] for item in calls] == ["New"]
    assert adapter.bridge.picker.selected("a", "telegram", "100", "100|41", data) == "known"
    assert adapter.bridge.picker.selected("a", "telegram", "100", "100|99", data) == "new"
    await adapter._sync_topics("a", msg, data)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_adapter_refuses_voice_before_scheduling(tmp_path, monkeypatch):
    from nanomuse.channels.base import InboundFile

    sent = []
    manager = SimpleNamespace(svc=SimpleNamespace(data_dir=tmp_path), channels={})

    async def reply(_channel, _chat, text):
        sent.append(text)

    async def identity(_msg):
        return "a", "key", "https://relay.test", "token"

    async def get_catalog(*_args):
        return catalog("a")

    manager._send = reply
    adapter = CloudMessengerAdapter(manager)
    monkeypatch.setattr(adapter, "_identity", identity)
    monkeypatch.setattr(adapter, "_catalog", get_catalog)
    msg = InboundMessage("max", "chat", "user", message_id="1", files=[InboundFile("voice.ogg", b"audio", "audio")])
    assert await adapter.handle(msg)
    assert "сохранено" in sent[-1] and not adapter.bridge.pending()
    operation = adapter.bridge.operation("a", "max", "user", "chat", "1")
    import base64
    assert base64.b64decode(adapter.bridge.attachments(operation, "a")[0]["data"]) == b"audio"


@pytest.mark.asyncio
async def test_adapter_preserves_file_but_never_starts_it_while_sync_is_unavailable(tmp_path, monkeypatch):
    import base64

    from nanomuse.channels.base import InboundFile

    sent = []
    manager = SimpleNamespace(svc=SimpleNamespace(data_dir=tmp_path), channels={})

    async def reply(_channel, _chat, text):
        sent.append(text)

    async def identity(_msg):
        return "a", "key", "https://relay.test", "token"

    async def must_not_read_catalog(*_args):
        raise AssertionError("file should be preserved without starting sync or model")

    manager._send = reply
    adapter = CloudMessengerAdapter(manager)
    monkeypatch.setattr(adapter, "_identity", identity)
    monkeypatch.setattr(adapter, "_catalog", must_not_read_catalog)
    msg = InboundMessage("max", "chat", "user", text="please read", message_id="file-1", files=[InboundFile("note.txt", b"secret file", "file")])
    assert await adapter.handle(msg)
    operation = adapter.bridge.operation("a", "max", "user", "chat", "file-1")
    assert base64.b64decode(adapter.bridge.attachments(operation, "a")[0]["data"]) == b"secret file"
    assert "сохранены" in sent[-1] and not adapter.bridge.pending()


@pytest.mark.asyncio
async def test_delivery_recovery_requires_same_sender_and_survives_lost_retry_response(tmp_path, monkeypatch):
    from nanomuse.channels.cloud_messenger_adapter import BridgeUnavailable

    sent = []
    manager = SimpleNamespace(svc=SimpleNamespace(data_dir=tmp_path), channels={})

    async def reply(_channel, chat, text):
        sent.append((chat, text))

    async def identity(_msg):
        return "a", "key", "https://relay.test", "token"

    async def get_catalog(*_args):
        return catalog("a")

    manager._send = reply
    adapter = CloudMessengerAdapter(manager)
    monkeypatch.setattr(adapter, "_identity", identity)
    monkeypatch.setattr(adapter, "_catalog", get_catalog)
    adapter.bridge.picker.choose("a", "max", "user", "chat", "shared-cid", catalog("a"))
    job = adapter.bridge.record("a", "max", "user", "chat", "question-1", "question", catalog("a"))
    adapter.bridge._transition(job["operation"], "queued", "accepted")
    adapter.bridge._transition(job["operation"], "accepted", "unknown")
    chunks = adapter._parts("answer")

    async def unknown_part(_index, _chunk):
        raise TimeoutError("the platform might have accepted it")

    await adapter.bridge.delivery.deliver("a", "max", "user", "chat", job["operation"], chunks, unknown_part)

    async def worker(_job, suffix):
        assert suffix == "status"
        return {"state": "completed", "text": "answer", "mid": "f43f49a5-7814-4320-9d63-866fe9f27d26", "files": []}

    async def prepare(_job, _chunks):
        return {"parts": [{"part": 0, "state": "unknown", "remote_id": ""}]}

    decisions = []

    async def relay(_job, action, body):
        assert action == "retry"
        decisions.append(body["decision"])
        if len(decisions) == 1:
            raise BridgeUnavailable("response lost")
        return {"state": "pending"}

    monkeypatch.setattr(adapter, "_worker", worker)
    monkeypatch.setattr(adapter, "_prepare_delivery", prepare)
    monkeypatch.setattr(adapter, "_relay_delivery", relay)
    assert await adapter.handle(InboundMessage("max", "chat", "user", text="/delivery"))
    assert await adapter.handle(InboundMessage("max", "chat", "user", text="/delivery_retry 1"))
    token = sent[-1][1].split("/delivery_confirm ")[1]
    assert await adapter.handle(InboundMessage("max", "chat", "other", text="/delivery_confirm " + token))
    assert not decisions
    assert await adapter.handle(InboundMessage("max", "chat", "user", text="/delivery_confirm " + token))
    assert len(decisions) == 1
    # Confirmation already began; even if its display lifetime passes, the same
    # idempotency decision must remain usable after an ambiguous relay response.
    import sqlite3
    with sqlite3.connect(adapter.bridge.path) as db:
        db.execute("UPDATE delivery_recoveries SET expires=0")
    adapter = CloudMessengerAdapter(manager)
    monkeypatch.setattr(adapter, "_identity", identity)
    monkeypatch.setattr(adapter, "_catalog", get_catalog)
    monkeypatch.setattr(adapter, "_worker", worker)
    monkeypatch.setattr(adapter, "_relay_delivery", relay)
    assert await adapter.handle(InboundMessage("max", "chat", "user", text="/delivery_confirm " + token))
    assert len(decisions) == 2 and decisions[0] == decisions[1]
    assert adapter.bridge.pending()[0]["state"] == "accepted"


@pytest.mark.asyncio
async def test_delivery_recovery_reconciles_confirmed_part_without_sending(tmp_path, monkeypatch):
    sent = []
    manager = SimpleNamespace(svc=SimpleNamespace(data_dir=tmp_path), channels={})

    async def reply(_channel, chat, text):
        sent.append((chat, text))

    async def identity(_msg):
        return "a", "key", "https://relay.test", "token"

    async def get_catalog(*_args):
        return catalog("a")

    manager._send = reply
    adapter = CloudMessengerAdapter(manager)
    monkeypatch.setattr(adapter, "_identity", identity)
    monkeypatch.setattr(adapter, "_catalog", get_catalog)
    adapter.bridge.picker.choose("a", "max", "user", "chat", "shared-cid", catalog("a"))
    job = adapter.bridge.record("a", "max", "user", "chat", "question-1", "question", catalog("a"))
    adapter.bridge._transition(job["operation"], "queued", "accepted")
    adapter.bridge._transition(job["operation"], "accepted", "unknown")
    chunks = adapter._parts("answer")

    async def lost(_index, _chunk):
        raise TimeoutError("lost platform response")

    await adapter.bridge.delivery.deliver("a", "max", "user", "chat", job["operation"], chunks, lost)

    async def worker(_job, _suffix):
        return {"state": "completed", "text": "answer", "mid": "f43f49a5-7814-4320-9d63-866fe9f27d26", "files": []}

    async def prepare(_job, _chunks):
        return {"parts": [{"part": 0, "state": "sent", "remote_id": "already-sent"}]}

    monkeypatch.setattr(adapter, "_worker", worker)
    monkeypatch.setattr(adapter, "_prepare_delivery", prepare)
    await adapter.handle(InboundMessage("max", "chat", "user", text="/delivery"))
    await adapter.handle(InboundMessage("max", "chat", "user", text="/delivery_retry 1"))
    assert "сверено" in sent[-1][1]
    assert adapter.bridge.pending()[0]["state"] == "accepted"

    async def never_send(_index, _chunk):
        raise AssertionError("already confirmed part must not be sent again")

    result = await adapter.bridge.delivery.deliver("a", "max", "user", "chat", job["operation"], chunks, never_send)
    assert result["state"] == "sent"


@pytest.mark.asyncio
async def test_unknown_command_does_not_fall_back_to_installation_chat(tmp_path, monkeypatch):
    sent = []
    manager = SimpleNamespace(svc=SimpleNamespace(data_dir=tmp_path), channels={})

    async def reply(_channel, _chat, text):
        sent.append(text)

    async def identity(_msg):
        return "a", "key", "https://relay.test", "token"

    manager._send = reply
    adapter = CloudMessengerAdapter(manager)
    monkeypatch.setattr(adapter, "_identity", identity)
    assert await adapter.handle(InboundMessage("telegram", "100", "100", text="/model other"))
    assert "команда пока недоступна" in sent[-1] and not adapter.bridge.pending()


def test_scheduler_never_creates_more_than_twenty_tasks(tmp_path):
    import sqlite3

    manager = SimpleNamespace(svc=SimpleNamespace(data_dir=tmp_path), channels={})
    created = []

    def spawn(coro):
        created.append(True)
        coro.close()

    manager._spawn = spawn
    adapter = CloudMessengerAdapter(manager)
    with sqlite3.connect(adapter.bridge.path) as db:
        db.executemany(
            "INSERT INTO jobs(operation,account,channel,sender,chat,message,conversation,text,state) "
            "VALUES(?,?,?,?,?,?,?,?, 'queued')",
            [(f"op-{i}", f"a-{i % 100}", "max", str(i), "chat", str(i), "cid", "text") for i in range(1000)],
        )
    adapter._schedule_due()
    adapter._schedule_due()
    assert len(created) == len(adapter._running) == 20


@pytest.mark.asyncio
async def test_outage_status_is_sent_once_and_request_stays_pending(tmp_path, monkeypatch):
    from nanomuse.channels.cloud_messenger_adapter import BridgeUnavailable

    sent = []
    manager = SimpleNamespace(svc=SimpleNamespace(data_dir=tmp_path), channels={})

    async def reply(_channel, _chat, text):
        sent.append(text)

    manager._send = reply
    adapter = CloudMessengerAdapter(manager)
    adapter.bridge.picker.choose("a", "max", "1", "chat", "shared-cid", catalog("a"))
    job = adapter.bridge.record("a", "max", "1", "chat", "message-1", "hello", catalog("a"))

    async def offline(_job):
        raise BridgeUnavailable("offline")

    monkeypatch.setattr(adapter, "_advance", offline)
    await adapter._resume_one(job)
    await adapter._resume_one(job)
    assert len(sent) == 1 and "сохранён" in sent[0]
    assert adapter.bridge.pending()[0]["state"] == "queued"


@pytest.mark.asyncio
async def test_opt_in_blocks_legacy_callback_before_local_menu():
    from nanomuse.channels.manager import ChannelManager

    seen = []
    manager = ChannelManager.__new__(ChannelManager)

    async def reject(msg):
        seen.append((msg.channel, msg.chat_id, msg.sender_id))

    async def legacy(*_args):
        raise AssertionError("legacy menu must not run for a cloud bridge action")

    manager._cloud_messenger = SimpleNamespace(reject_legacy_action=reject)
    manager._allowed = lambda _msg: True
    manager.menu = SimpleNamespace(callback=legacy)
    await manager._handle_action("telegram", "100|1", "100", {"menu": "settings"})
    assert seen == [("telegram", "100|1", "100")]
