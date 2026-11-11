"""Messenger files are durable, bounded and bound to the verified operation."""

from __future__ import annotations

import pytest

from nanomuse.channels.base import InboundFile
from nanomuse.channels.cloud_messenger import CloudMessengerBridge, JobConflict
from nanomuse.messenger_attachments import MAX_FILE_BYTES, AttachmentError, parse_files


def catalog(account):
    return {"account_id": account, "complete": True, "conversations": [{"cid": "same", "archived": False}]}


def test_files_survive_restart_are_account_scoped_and_conflicts_rejected(tmp_path):
    bridge = CloudMessengerBridge(tmp_path)
    for account in ("a", "b"):
        bridge.picker.choose(account, "telegram", account, account + "|5", "same", catalog(account))
    original = [InboundFile("report.txt", b"private-a")]
    a = bridge.record("a", "telegram", "a", "a|5", "1", "", catalog("a"), original)
    b = bridge.record("b", "telegram", "b", "b|5", "1", "", catalog("b"), [InboundFile("report.txt", b"private-b")])
    bridge = CloudMessengerBridge(tmp_path)
    assert parse_files(bridge.attachments(a["operation"], "a"))[0]["data"] == b"private-a"
    assert parse_files(bridge.attachments(b["operation"], "b"))[0]["data"] == b"private-b"
    with pytest.raises(AttachmentError):
        bridge.attachments(a["operation"], "b")
    with pytest.raises(JobConflict):
        bridge.record("a", "telegram", "a", "a|5", "1", "", catalog("a"), [InboundFile("report.txt", b"changed")])
    assert bridge.record("a", "telegram", "a", "a|5", "1", "", catalog("a"), original)["operation"] == a["operation"]


def test_oversize_or_path_names_are_refused_without_a_job(tmp_path):
    bridge = CloudMessengerBridge(tmp_path)
    bridge.picker.choose("a", "max", "a", "chat", "same", catalog("a"))
    for file in [InboundFile("../private", b"x"), InboundFile("large", b"x" * (MAX_FILE_BYTES + 1))]:
        with pytest.raises(AttachmentError):
            bridge.record("a", "max", "a", "chat", "1", "", catalog("a"), [file])
    assert not bridge.pending()


def test_thread_storage_is_private_and_rolls_back_on_write_error(tmp_path, monkeypatch):
    import os
    from types import SimpleNamespace

    from nanomuse.messenger_attachments import prepare_files, save_to_thread

    def svc(name):
        root = tmp_path / name
        root.mkdir()
        return SimpleNamespace(data_dir=root, workspace_for_thread=lambda thread, scope: root / "workspaces" / scope / thread)

    a, b = svc("a"), svc("b")
    inputs = prepare_files([InboundFile("report.txt", b"one"), InboundFile("two.txt", b"two")])
    paths_a = save_to_thread(a, "thread-a", "account-a", "operation_123456789", inputs)
    paths_b = save_to_thread(b, "thread-b", "account-b", "operation_123456789", inputs)
    assert paths_a != paths_b
    assert sorted(x.read_bytes() for x in (a.data_dir / "workspaces").rglob("*.txt")) == [b"one", b"two"]
    assert sorted(x.read_bytes() for x in (b.data_dir / "workspaces").rglob("*.txt")) == [b"one", b"two"]
    actual_fsync = os.fsync
    count = 0
    def failure(fd):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("disk failure")
        return actual_fsync(fd)
    monkeypatch.setattr(os, "fsync", failure)
    with pytest.raises(AttachmentError):
        save_to_thread(a, "thread-a", "account-a", "other_operation_123", inputs)
    assert len(list((a.data_dir / "workspaces").rglob("*.txt"))) == 2


def test_thread_storage_refuses_symlink_escape(tmp_path):
    from types import SimpleNamespace

    from nanomuse.messenger_attachments import prepare_files, save_to_thread

    root = tmp_path / "a"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "workspaces").symlink_to(outside, target_is_directory=True)
    svc = SimpleNamespace(data_dir=root, workspace_for_thread=lambda _t, _s: root / "workspaces" / "scope")
    with pytest.raises(AttachmentError):
        save_to_thread(svc, "thread-a", "account-a", "operation_123456789", prepare_files([InboundFile("file.txt", b"x")]))
    assert not list(outside.iterdir())


@pytest.mark.asyncio
async def test_attachment_response_goes_only_to_original_topic_and_unknown_is_not_retried(tmp_path):
    from nanomuse.messenger_attachments import prepare_files, wire_files

    bridge = CloudMessengerBridge(tmp_path)
    bridge.picker.choose("a", "telegram", "a", "a|5", "same", catalog("a"))
    job = bridge.record("a", "telegram", "a", "a|5", "1", "question", catalog("a"))
    async def accepted(_job):
        return {"state": "accepted"}
    async def done(_job):
        return {"state": "completed", "text": "Готово", "mid": "answer-mid", "files": wire_files(prepare_files([InboundFile("result.txt", b"private")]))}
    sent = []
    async def deliver(job, index, chunk):
        sent.append((job["account"], job["chat"], job["response_mid"], index, chunk))
        return "telegram-id"
    assert await bridge.advance(job, catalog("a"), accepted, done, lambda text: [text], deliver) == "done"
    assert len(sent) == 2 and all(row[:3] == ("a", "a|5", "answer-mid") for row in sent)
    assert parse_files([{k: __import__("json").loads(sent[1][4])[k] for k in ("name", "kind", "sha256", "data")}])[0]["data"] == b"private"


@pytest.mark.asyncio
async def test_worker_refuses_files_before_runtime_and_archived_text_before_dispatch(tmp_path, monkeypatch):
    import uuid
    from types import SimpleNamespace

    import httpx

    from nanomuse.messenger_attachments import prepare_files, wire_files
    from nanomuse.server import cloud_worker
    from nanomuse.server.accounts import AccountRuntime
    from nanomuse.server.events import Timeline

    accounts = [str(uuid.uuid4()), str(uuid.uuid4())]
    monkeypatch.setenv("NANOMUSE_ACCOUNTS_DIR", str(tmp_path / "journal"))
    monkeypatch.setattr(cloud_worker, "WORKER_SECRET", "fixture-secret")
    monkeypatch.setattr(cloud_worker, "MESSENGER_SECRET", "messenger-secret-32-characters-long")

    class Sync:
        active = True
        def __init__(self, account):
            self.account_id = account
            self.archived = False
        def thread_of(self, cid):
            return self.thread if cid == "cid" else None
        def owner_of(self, _thread):
            return self.account_id
        def cid_of(self, _thread):
            return "cid"
        def _eligible(self, _thread):
            return True
        async def pull(self):
            pass
        async def hydrate_thread(self, _thread, _cid):
            if self.archived:
                raise ValueError("archived")

    class Service:
        def __init__(self, account):
            self.data_dir = tmp_path / account
            self.data_dir.mkdir()
            self.sync = Sync(account)
            self.sync.thread = SimpleNamespace(id="thread", timeline=Timeline("thread", self.data_dir / "events.json"))
            self.calls = []
        def workspace_for_thread(self, thread, scope):
            return self.data_dir / "workspaces" / scope / thread
        def resolve_workspace_path(self, rel, thread_id="", customer_scope=""):
            root = self.workspace_for_thread(thread_id, customer_scope)
            prefix = f"threads/{thread_id}/"
            return (root / rel.removeprefix(prefix)).resolve()
        def send(self, thread_id, text, **kwargs):
            self.calls.append(kwargs)
            scope = kwargs["customer_scope"]
            path = self.workspace_for_thread(thread_id, scope) / "out.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("result-" + self.sync.account_id).encode())
            user = kwargs["user_event_id"]
            timeline = self.sync.thread.timeline
            timeline.add({"id": user, "mid": kwargs["user_mid"], "type": "user", "text": text,
                          "files": [{"path": path} for path in kwargs["files"]]})
            events = timeline.events
            import hashlib
            events.append({"type": "artifact", "path": "out.txt", "reply_to_event": user,
                           "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": path.stat().st_size})
            events.append({"type": "artifact", "path": "foreign.txt", "reply_to_event": "other"})
            events.append({"type": "assistant", "final": True, "text": "Готово", "mid": "answer-" + self.sync.account_id, "reply_to_event": user})
            return {"id": user, "mid": kwargs["user_mid"]}

    services = {account: Service(account) for account in accounts}
    runtime_accounts = []
    async def get_service(_self, account, _key):
        runtime_accounts.append(account)
        return services[account]
    monkeypatch.setattr(AccountRuntime, "service", get_service)
    app = cloud_worker.build_worker()
    data = wire_files(prepare_files([InboundFile("input.txt", b"personal input")]))
    body = {"operation_id": "telegram_message_files_123", "conversation_id": "cid", "text": "", "files": data}
    base = {"X-Worker-Secret": "fixture-secret", "X-Messenger-Secret": "messenger-secret-32-characters-long", "X-Cloud-Key": "key"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://worker") as client:
        for account in accounts:
            headers = {**base, "X-Account": account}
            first = await client.post("/internal/messenger/messages", headers=headers, json=body)
            again = await client.post("/internal/messenger/messages", headers=headers, json=body)
            assert first.status_code == again.status_code == 200
            assert first.json() == again.json() == {"state": "rejected", "reason": "attachments_disabled"}
            assert not services[account].calls
            assert not (services[account].data_dir / "workspaces").exists()
        assert not runtime_accounts
        assert (await client.get("/internal/messenger/messages/telegram_message_files_123", headers={**base, "X-Account": str(uuid.uuid4())})).status_code == 404
        services[accounts[0]].sync.archived = True
        refused = await client.post("/internal/messenger/messages", headers={**base, "X-Account": accounts[0]}, json={**body, "operation_id": "archived_message_text_123", "text": "hello", "files": []})
        assert refused.status_code == 503 and refused.json()["detail"] == "conversation history unavailable"
        assert runtime_accounts == [accounts[0]] and not services[accounts[0]].calls


def test_durable_attachment_quota_refuses_without_partial_job(tmp_path, monkeypatch):
    from nanomuse.channels import cloud_messenger
    from nanomuse.channels.cloud_messenger import StorageFull

    monkeypatch.setattr(cloud_messenger, "MAX_ACCOUNT_STORED", 5)
    bridge = CloudMessengerBridge(tmp_path)
    bridge.picker.choose("a", "max", "a", "chat", "same", catalog("a"))
    bridge.record("a", "max", "a", "chat", "1", "", catalog("a"), [InboundFile("a.txt", b"1234")])
    with pytest.raises(StorageFull):
        bridge.record("a", "max", "a", "chat", "2", "", catalog("a"), [InboundFile("b.txt", b"12")])
    assert len(bridge.pending()) == 1


@pytest.mark.asyncio
async def test_rejected_before_dispatch_releases_temporary_file_bytes(tmp_path):
    bridge = CloudMessengerBridge(tmp_path)
    bridge.picker.choose("a", "max", "a", "chat", "same", catalog("a"))
    file = InboundFile("report.txt", b"private-bytes")
    job = bridge.record("a", "max", "a", "chat", "1", "", catalog("a"), [file])
    async def rejected(_job):
        return {"state": "rejected"}
    async def never(*_args):
        raise AssertionError("should not send or poll")
    assert await bridge.advance(job, catalog("a"), rejected, never, lambda s: [s], never) == "rejected"
    with pytest.raises(AttachmentError):
        bridge.attachments(job["operation"], "a")
    assert bridge.record("a", "max", "a", "chat", "1", "", catalog("a"), [file])["state"] == "rejected"


@pytest.mark.asyncio
async def test_durable_confirmation_waits_for_old_timeline_write(tmp_path):
    import asyncio
    import json
    import threading
    from types import SimpleNamespace

    from nanomuse.server.events import Timeline
    from nanomuse.server.messenger_ops import confirm_user_durable

    timeline = Timeline("thread", tmp_path / "events.json")
    started, release = threading.Event(), threading.Event()
    original_write = timeline._write
    old_payload = json.dumps({"thread": "thread", "events": []})
    def stale_writer():
        started.set()
        release.wait(timeout=5)
        original_write(old_payload)
    timeline._writing = asyncio.get_running_loop().run_in_executor(None, stale_writer)
    assert await asyncio.to_thread(started.wait, 2)
    timeline.add({"id": "user-1", "mid": "mid-1", "type": "user"})
    task = asyncio.create_task(confirm_user_durable(SimpleNamespace(timeline=timeline), "user-1", "mid-1", []))
    await asyncio.sleep(0.05)
    assert not task.done()
    release.set()
    await task
    assert json.loads(timeline.path.read_text())["events"][0]["mid"] == "mid-1"


@pytest.mark.asyncio
async def test_changed_artifact_is_never_sent_as_original_response(tmp_path):
    import hashlib
    from types import SimpleNamespace

    from nanomuse.messenger_attachments import read_from_thread

    root = tmp_path / "account"
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    result = workspace / "result.txt"
    result.write_bytes(b"original")
    event = {"path": "result.txt", "sha256": hashlib.sha256(b"original").hexdigest(), "size": 8}
    svc = SimpleNamespace(data_dir=root, workspace_for_thread=lambda *_args: workspace,
                          resolve_workspace_path=lambda path, **_kwargs: workspace / path)
    assert parse_files(read_from_thread(svc, "chat", "account:a", [event]))[0]["data"] == b"original"
    result.write_bytes(b"replaced")  # same size, different contents
    with pytest.raises(AttachmentError):
        read_from_thread(svc, "chat", "account:a", [event])


@pytest.mark.asyncio
@pytest.mark.parametrize("channel,chat", [("telegram", "42|5"), ("max", "max-chat")])
async def test_actual_file_part_uses_source_topic_and_removes_temporary_file(tmp_path, channel, chat):
    import json
    from types import SimpleNamespace

    from nanomuse.channels.cloud_messenger_adapter import CloudMessengerAdapter
    from nanomuse.messenger_attachments import prepare_files, wire_files

    seen = []
    async def send_file(destination, path):
        seen.append((destination, path, path.name, path.read_bytes()))
        return "platform-123"
    manager = SimpleNamespace(svc=SimpleNamespace(data_dir=tmp_path), channels={channel: SimpleNamespace(_send_file=send_file)})
    adapter = CloudMessengerAdapter(manager)
    payload = wire_files(prepare_files([InboundFile("ответ.txt", b"correct account")]))[0]
    result = await adapter._platform_part({"channel": channel, "chat": chat, "message": "1"}, 0, json.dumps({"type": "file", **payload}))
    assert result == "platform-123"
    assert seen[0][0] == chat and seen[0][2:] == ("ответ.txt", b"correct account")
    assert not seen[0][1].exists()


def test_wire_format_rejects_tampering_and_preserves_empty_files():
    from nanomuse.messenger_attachments import prepare_files, wire_files

    empty = wire_files(prepare_files([InboundFile("empty.txt", b"")]))
    assert parse_files(empty)[0]["data"] == b""
    for bad in (
        {**empty[0], "data": "%%%"},
        {**empty[0], "sha256": "0" * 64},
        {**empty[0], "kind": []},
        {**empty[0], "name": "../file"},
    ):
        with pytest.raises(AttachmentError):
            parse_files([bad])


def test_result_read_rejects_parent_symlink_even_if_swapped_during_open(tmp_path, monkeypatch):
    import hashlib
    import os
    from types import SimpleNamespace

    from nanomuse.messenger_attachments import read_from_thread

    root = tmp_path / "account"
    workspace = root / "workspace"
    folder = workspace / "folder"
    folder.mkdir(parents=True)
    (folder / "result.txt").write_bytes(b"safe")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "result.txt").write_bytes(b"safe")
    svc = SimpleNamespace(data_dir=root, workspace_for_thread=lambda *_args: workspace)
    event = {"path": "folder/result.txt", "sha256": hashlib.sha256(b"safe").hexdigest(), "size": 4}
    actual_open = os.open
    swapped = False
    def swap_before_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if path == "folder" and dir_fd is not None and not swapped:
            swapped = True
            folder.rename(workspace / "old-folder")
            folder.symlink_to(outside, target_is_directory=True)
        return actual_open(path, flags, mode, dir_fd=dir_fd)
    monkeypatch.setattr(os, "open", swap_before_open)
    with pytest.raises(AttachmentError):
        read_from_thread(svc, "chat", "account:a", [event])
    assert swapped
