"""Internal messenger admission is authenticated and never repeats an uncertain turn."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import pytest

from nanomuse.server import cloud_worker
from nanomuse.server.accounts import AccountRuntime


@pytest.mark.asyncio
async def test_worker_messenger_admission_and_retry(tmp_path, monkeypatch):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    calls = []
    monkeypatch.setenv("NANOMUSE_ACCOUNTS_DIR", str(tmp_path))
    monkeypatch.setattr(cloud_worker, "WORKER_SECRET", "fixture-secret")
    monkeypatch.setattr(cloud_worker, "MESSENGER_SECRET", "messenger-secret-32-characters-long")

    from nanomuse.server.events import Timeline

    class Sync:
        active = True

        def __init__(self, owner):
            self.account_id = owner
            self.thread = SimpleNamespace(id="chat", timeline=Timeline("chat", tmp_path / owner / "events.json"))

        def owner_of(self, thread_id):
            return self.account_id if thread_id == "chat" else "another-account"

        def cid_of(self, _thread_id):
            return "cid-123"

        def thread_of(self, cid):
            return self.thread if cid == "cid-123" else None

        async def pull(self):
            return None

        def _eligible(self, _thread):
            return True

        async def hydrate_thread(self, _thread, _cid):
            pass

    class Service:
        def __init__(self, account):
            self.sync = Sync(account)
            self.threads = {"chat": SimpleNamespace()}
            self.account = account

        def messenger_preflight(self, _thread_id, _text, _files):
            return None

        def send(self, thread_id, text, **kwargs):
            calls.append((self.account, thread_id, text, kwargs))
            if text == "crash after dispatch":
                raise RuntimeError("simulated uncertain result")
            event = {"id": kwargs["user_event_id"], "mid": kwargs["user_mid"], "type": "user", "text": text}
            self.sync.thread.timeline.add(event)
            return event

    services = {account: Service(account) for account in (a, b)}

    async def service(_self, account, _key):
        return services[account]

    monkeypatch.setattr(AccountRuntime, "service", service)
    app = cloud_worker.build_worker()
    payload = {"operation_id": "telegram_message_123", "conversation_id": "cid-123", "text": "hello"}
    headers = {"X-Worker-Secret": "fixture-secret", "X-Messenger-Secret": "messenger-secret-32-characters-long", "X-Account": a, "X-Cloud-Key": "key-a"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://worker") as c:
        assert (await c.post("/internal/messenger/messages", json=payload)).status_code == 403
        assert (await c.post("/internal/messenger/messages", headers={**headers, "X-Messenger-Secret": ""}, json=payload)).status_code == 403
        assert (await c.post("/internal/messenger/messages", headers={**headers, "X-Cloud-Key": ""}, json=payload)).status_code == 401
        first = await c.post("/internal/messenger/messages", headers=headers, json=payload)
        repeat = await c.post("/internal/messenger/messages", headers=headers, json=payload)
        assert first.status_code == repeat.status_code == 200
        assert first.json() == repeat.json()
        assert first.json()["state"] == "accepted" and first.json()["thread_id"] == "cid-123"
        assert first.json()["event_id"].startswith("e_")
        assert (await c.post("/internal/messenger/messages", headers=headers, json={**payload, "text": "different"})).status_code == 409
        other = await c.post("/internal/messenger/messages", headers={**headers, "X-Account": b, "X-Cloud-Key": "key-b"}, json=payload)
        assert other.status_code == 200 and other.json()["event_id"] != first.json()["event_id"]
        missing = await c.post("/internal/messenger/messages", headers=headers, json={**payload, "operation_id": "different_message_123", "conversation_id": "foreign"})
        assert missing.status_code == 404
        uncertain = {**payload, "operation_id": "telegram_uncertain_123", "text": "crash after dispatch"}
        assert (await c.post("/internal/messenger/messages", headers=headers, json=uncertain)).status_code == 503
        assert (await c.post("/internal/messenger/messages", headers=headers, json=uncertain)).status_code == 409
    assert len(calls) == 3
    assert calls[0][3]["once_key"] == "key-a" and calls[1][3]["once_key"] == "key-b"
    assert calls[0][3]["customer_scope"] == f"account:{a}"
    # An uncertain outcome after process restart is reported; it is never sent a second time.
    app2 = cloud_worker.build_worker()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app2), base_url="http://worker") as c:
        repeat = await c.post("/internal/messenger/messages", headers=headers, json=payload)
        assert repeat.status_code == 409 and "not repeated" in repeat.json()["detail"]
    assert len(calls) == 3
    assert (tmp_path / "messenger-operations.sqlite3").stat().st_mode & 0o777 == 0o600
    assert b"key-a" not in (tmp_path / "messenger-operations.sqlite3").read_bytes()


@pytest.mark.asyncio
async def test_worker_messenger_forbids_unconfigured_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("NANOMUSE_ACCOUNTS_DIR", str(tmp_path))
    monkeypatch.setattr(cloud_worker, "WORKER_SECRET", "")
    app = cloud_worker.build_worker()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://worker") as c:
        response = await c.post("/internal/messenger/messages", headers={"X-Account": str(uuid.uuid4()), "X-Cloud-Key": "key"}, json={"operation_id": "telegram_message_123", "conversation_id": "cid-123", "text": "hello"})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_internal_endpoint_reaches_real_service_with_customer_key(settings, tmp_path, monkeypatch):
    import asyncio
    from unittest.mock import PropertyMock

    from nanomuse.llm import MockLLM
    from nanomuse.schema import LLMResponse
    from nanomuse.server.service import MuseService

    account = str(uuid.uuid4())
    monkeypatch.setenv("NANOMUSE_ACCOUNTS_DIR", str(tmp_path / "operations"))
    monkeypatch.setattr(cloud_worker, "WORKER_SECRET", "fixture-secret")
    monkeypatch.setattr(cloud_worker, "MESSENGER_SECRET", "messenger-secret-32-characters-long")
    model = MockLLM([LLMResponse(content="private answer")])
    service = MuseService(settings, llm=model)
    service.timeline("chat")
    thread = service.threads["chat"]
    service.sync.state["account_id"] = account
    service.sync.state["owners"]["chat"] = account
    service.sync.state["cids"]["chat"] = "cloud-chat"
    monkeypatch.setattr(type(service.sync), "active", PropertyMock(return_value=True))
    monkeypatch.setattr(service.sync, "_eligible", lambda _thread: True)
    monkeypatch.setattr(service.sync, "message_sent", lambda _thread: None)

    async def hydrate(_thread, _cid, **_kwargs):
        return None

    async def lease(_thread):
        return "cloud-chat", "turn-test"

    async def hydrate_pull():
        return None

    monkeypatch.setattr(service.sync, "hydrate_thread", hydrate)
    monkeypatch.setattr(service.sync, "pull", hydrate_pull)
    monkeypatch.setattr(service, "_lease_turn_strict", lease)
    seen = []

    def customer_model(key, *_args, **_kwargs):
        seen.append(key)
        return model, "test-model"

    monkeypatch.setattr(service, "cloud_once", customer_model)

    async def get_service(_self, _account, _key):
        return service

    monkeypatch.setattr(AccountRuntime, "service", get_service)
    app = cloud_worker.build_worker()
    headers = {"X-Worker-Secret": "fixture-secret", "X-Messenger-Secret": "messenger-secret-32-characters-long", "X-Account": account, "X-Cloud-Key": "personal-key"}
    body = {"operation_id": "telegram_message_real_123", "conversation_id": "cloud-chat", "text": "personal question"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://worker") as client:
        result = await client.post("/internal/messenger/messages", headers=headers, json=body)
        assert result.status_code == 200
        await asyncio.wait_for(thread.worker, timeout=10)
        repeat = await client.post("/internal/messenger/messages", headers=headers, json=body)
        finished = await client.get("/internal/messenger/messages/telegram_message_real_123", headers=headers)
        foreign = await client.get("/internal/messenger/messages/telegram_message_real_123", headers={**headers, "X-Account": str(uuid.uuid4())})
        thread.timeline.events.append({"type": "artifact", "reply_to_event": result.json()["event_id"], "path": "secret.txt"})
        disabled_files = await client.get("/internal/messenger/messages/telegram_message_real_123", headers=headers)
        disabled_files_again = await client.get("/internal/messenger/messages/telegram_message_real_123", headers=headers)
    assert repeat.json() == result.json()
    assert finished.status_code == 200
    assert finished.json()["state"] == "completed" and finished.json()["text"] == "private answer"
    assert foreign.status_code == 404
    assert disabled_files.json()["state"] == "failed"
    assert disabled_files.json()["files"] == []
    assert "передача" in disabled_files.json()["text"]
    assert "secret.txt" not in disabled_files.text
    assert disabled_files.json()["mid"] == disabled_files_again.json()["mid"]
    assert disabled_files.json()["mid"] != finished.json()["mid"]
    assert seen == ["personal-key"]
    assert len([e for e in thread.timeline.events if e.get("text") == "personal question"]) == 1
    answer = next(e for e in thread.timeline.events if e.get("text") == "private answer")
    assert answer.get("reply_to_event") == result.json()["event_id"]


def test_only_unclaimed_operation_can_resume_after_restart(tmp_path):
    from nanomuse.server.messenger_ops import MessengerOperations

    payload = {"thread_id": "chat", "text": "recover me", "user_event_id": "e_stable", "user_mid": "stable-mid"}
    first = MessengerOperations(tmp_path / "ops.db", "process-one")
    assert first.reserve("account", "operation-123456", payload)["state"] == "queued"
    resumed = MessengerOperations(tmp_path / "ops.db", "process-two")
    assert resumed.reserve("account", "operation-123456", payload)["state"] == "queued"
    assert resumed.claim("account", "operation-123456")
    assert not first.claim("account", "operation-123456")
    restarted = MessengerOperations(tmp_path / "ops.db", "process-three")
    assert restarted.reserve("account", "operation-123456", payload)["state"] == "unknown"
    assert not restarted.claim("account", "operation-123456")


@pytest.mark.asyncio
async def test_worker_explicitly_refuses_attachments_before_starting_runtime(tmp_path, monkeypatch):
    from nanomuse.channels.base import InboundFile
    from nanomuse.messenger_attachments import prepare_files, wire_files

    monkeypatch.setenv("NANOMUSE_ACCOUNTS_DIR", str(tmp_path))
    monkeypatch.setattr(cloud_worker, "WORKER_SECRET", "worker-secret")
    monkeypatch.setattr(cloud_worker, "MESSENGER_SECRET", "messenger-secret-32-characters-long")

    async def never_start(*_args):
        raise AssertionError("disabled attachments must not start a runtime or model")

    monkeypatch.setattr(AccountRuntime, "service", never_start)
    app = cloud_worker.build_worker()
    headers = {"X-Worker-Secret": "worker-secret", "X-Messenger-Secret": "messenger-secret-32-characters-long", "X-Account": str(uuid.uuid4()), "X-Cloud-Key": "key"}
    body = {"operation_id": "telegram_message_files_123", "conversation_id": "cid", "text": "read", "files": wire_files(prepare_files([InboundFile("a.txt", b"text")]))}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://worker") as client:
        result = await client.post("/internal/messenger/messages", headers=headers, json=body)
    assert result.json() == {"state": "rejected", "reason": "attachments_disabled"}
