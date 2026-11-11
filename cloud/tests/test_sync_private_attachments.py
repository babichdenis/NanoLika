"""Account/conversation scoped cloud copies use the sync database transaction."""

from __future__ import annotations

import asyncio
import base64
import uuid

import httpx
import nanomuse.server  # noqa: F401  # initializes the sync package
import pytest
from fastapi import Header, HTTPException
from nanomuse.cloud import CloudClient
from nanomuse.cloud import CloudError as ClientCloudError
from nanomuse.sync.client import SyncClient
from nanomuse.sync.private_files import PrivateSyncFiles
from test_accounts import auth, make
from test_cloud import sign_up
from test_sync import conv

from nanomuse_cloud.private_attachments import PrivateFileError
from nanomuse_cloud.service import CloudError
from nanomuse_cloud.sync_private_attachments import ConversationFiles, install_conversation_files

SECRET = "Aa0123456789BbCcDdEeFfGgHhIiJjKkLlMmNnOoPpQqRrSs"


async def setup_app():
    app, client, sender, _up, cloud, _settings = make()
    a = await sign_up(client, sender, "file-a@example.test", "laptop-a")
    b = await sign_up(client, sender, "file-b@example.test", "laptop-b")
    cid, other = str(uuid.uuid4()), str(uuid.uuid4())
    for signed in (a, b):
        key = signed["api_key"]
        assert (await client.put("/v1/sync/state", json={"enabled": True}, headers=auth(key))).status_code == 200
        body = {"device": "test", "conversations": [conv(cid), conv(other)]}
        assert (await client.post("/v1/sync/changes", json=body, headers=auth(key))).status_code == 200
    store = ConversationFiles(cloud.db, SECRET)
    async def caller(authorization: str = Header(default="")):
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "sign in")
        try:
            return cloud.authenticate(authorization[7:])
        except CloudError:
            raise HTTPException(401, "sign in") from None
    install_conversation_files(app, store, caller)
    return app, client, cloud, store, a, b, cid, other


def upload_headers(key, op="upload_operation_123", name="a.txt"):
    encoded = base64.urlsafe_b64encode(name.encode()).decode()
    return {**auth(key), "Idempotency-Key": op, "Content-Type": "text/plain", "X-File-Name-Base64": encoded}


@pytest.mark.asyncio
async def test_second_device_gets_exact_bytes_only_for_its_account_and_conversation():
    _app, client, cloud, store, a, b, cid, other = await setup_app()
    key_a, key_b = a["api_key"], b["api_key"]
    account_a = a["account"]["id"]
    key_a2, _ = cloud._issue_key(account_a, "other-device", "code")
    path = f"/v1/sync/conversations/{cid}/files"
    sent = await client.post(path, headers=upload_headers(key_a, name="личный.txt"), content=b"private bytes")
    assert sent.status_code == 200, sent.text
    metadata = sent.json()
    ident = metadata["file_id"]
    assert metadata["cid"] == cid and metadata["expires_at"] is None
    own = await client.get(path + "/" + ident, headers=auth(key_a2))
    assert own.status_code == 200 and own.content == b"private bytes"
    assert own.headers["cache-control"] == "no-store"
    assert (await client.get(path + "/" + ident, headers=auth(key_b))).status_code == 404
    assert (await client.get(f"/v1/sync/conversations/{other}/files/{ident}", headers=auth(key_a))).status_code == 404
    assert (await client.get(path + "/" + ident)).status_code == 401
    personal, _ = cloud.create_user_key(cloud.authenticate(key_a), "agent")
    assert (await client.get(path + "/" + ident, headers=auth(personal))).status_code == 403
    # Archiving does not erase file data or reactivate a conversation.
    app_sync = _app.state.sync
    app_sync.set_archived(account_a, cid, True)
    assert (await client.get(path + "/" + ident, headers=auth(key_a2))).content == b"private bytes"
    with cloud.db.tx() as c:
        assert c.execute("SELECT archived FROM sync_conversations WHERE account_id=? AND cid=?", (account_a, cid)).fetchone()[0] == 1
    assert store.get(account_a, cid, ident)[1] == b"private bytes"
    await client.aclose()


@pytest.mark.asyncio
async def test_sync_off_deletes_only_cloud_copy_and_local_restores_after_reenable(tmp_path):
    _app, client, cloud, store, a, _b, cid, _other = await setup_app()
    key, account = a["api_key"], a["account"]["id"]
    local = tmp_path / "personal-workspace" / "original.txt"
    local.parent.mkdir()
    local.write_bytes(b"original on server")
    path = f"/v1/sync/conversations/{cid}/files"
    first = await client.post(path, headers=upload_headers(key, name="original.txt"), content=local.read_bytes())
    ident = first.json()["file_id"]
    assert (await client.get("/v1/sync/state", headers=auth(key))).json()["private_generation"] == 0
    assert (await client.put("/v1/sync/state", json={"enabled": False}, headers=auth(key))).status_code == 200
    assert local.read_bytes() == b"original on server"
    with cloud.db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM sync_private_files WHERE account_id=?", (account,)).fetchone()[0] == 0
    assert (await client.get(path + "/" + ident, headers=auth(key))).status_code == 404
    assert (await client.put("/v1/sync/state", json={"enabled": True}, headers=auth(key))).status_code == 200
    assert (await client.get("/v1/sync/changes?since=0", headers=auth(key))).json()["private_generation"] == 1
    assert (await client.get("/v1/sync/state", headers=auth(_b["api_key"]))).json()["private_generation"] == 0
    assert (await client.post("/v1/sync/changes", json={"device": "test", "conversations": [conv(cid)]}, headers=auth(key))).status_code == 200
    old_retry = await client.post(path, headers=upload_headers(key, name="original.txt"), content=local.read_bytes())
    assert old_retry.status_code == 409
    restored = await client.post(path, headers=upload_headers(key, "restore_operation_123", "original.txt"), content=local.read_bytes())
    assert restored.status_code == 200 and restored.json()["file_id"] != ident
    assert (await client.get(path + "/" + restored.json()["file_id"], headers=auth(key))).content == b"original on server"
    await client.aclose()


@pytest.mark.asyncio
async def test_transaction_rollback_and_explicit_delete_of_conversation_and_account():
    _app, client, cloud, store, a, b, cid, _other = await setup_app()
    account_a, account_b = a["account"]["id"], b["account"]["id"]
    key_a, key_b = a["api_key"], b["api_key"]
    first = store.put(account_a, cid, "operation_000000001", "a.txt", "text/plain", b"A")
    second = store.put(account_b, cid, "operation_000000001", "b.txt", "text/plain", b"B")
    with pytest.raises(RuntimeError):
        with cloud.db.tx() as c:
            c.execute("UPDATE accounts SET sync_enabled=0 WHERE id=?", (account_a,))
            raise RuntimeError("abort")
    assert store.get(account_a, cid, first["file_id"])[1] == b"A"
    deleted = await client.delete(f"/v1/sync/conversations/{cid}", headers=auth(key_a))
    assert deleted.status_code == 200
    with cloud.db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM sync_private_files WHERE account_id=?", (account_a,)).fetchone()[0] == 0
    assert store.get(account_b, cid, second["file_id"])[1] == b"B"
    assert (await client.post("/v1/auth/delete", headers=auth(key_b))).status_code == 204
    with cloud.db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM sync_private_files WHERE account_id=?", (account_b,)).fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM sync_private_operations WHERE account_id=?", (account_b,)).fetchone()[0] == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_concurrent_disable_never_leaves_a_cloud_copy():
    _app, client, cloud, store, a, _b, cid, _other = await setup_app()
    account = a["account"]["id"]
    key = a["api_key"]
    async def upload():
        return await client.post(f"/v1/sync/conversations/{cid}/files", headers=upload_headers(key, name="race.txt"), content=b"private")
    async def disable():
        return await client.put("/v1/sync/state", json={"enabled": False}, headers=auth(key))
    await asyncio.gather(upload(), disable())
    with cloud.db.tx() as c:
        assert c.execute("SELECT sync_enabled FROM accounts WHERE id=?", (account,)).fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM sync_private_files WHERE account_id=?", (account,)).fetchone()[0] == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_idempotent_upload_explicit_delete_and_size_limit():
    _app, client, _cloud, store, a, b, cid, _other = await setup_app()
    path = f"/v1/sync/conversations/{cid}/files"
    headers = upload_headers(a["api_key"], "operation_123456789")
    first, repeat = await asyncio.gather(*[
        client.post(path, headers=headers, content=b"same")
        for _ in range(2)
    ])
    assert first.status_code == repeat.status_code == 200
    ident = first.json()["file_id"]
    assert repeat.json()["file_id"] == ident
    conflict = await client.post(path, headers=headers, content=b"changed")
    assert conflict.status_code == 409
    too_large = await client.post(path, headers=upload_headers(a["api_key"], "operation_large_123", "huge"), content=b"x" * (store.max_file_bytes + 1))
    assert too_large.status_code == 413
    assert (await client.delete(path + "/" + ident, headers=auth(b["api_key"]))).status_code == 404
    assert (await client.delete(path + "/" + ident, headers=auth(a["api_key"]))).status_code == 204
    assert (await client.get(path + "/" + ident, headers=auth(a["api_key"]))).status_code == 404
    assert (await client.post(path, headers=headers, content=b"same")).status_code == 409
    await client.aclose()


def test_restart_recovers_encrypted_bytes_and_can_rotate_keys(tmp_path):
    from nanomuse_cloud.db import Database
    from nanomuse_cloud.sync import SyncStore

    db_path = tmp_path / "cloud.sqlite"
    db = Database(str(db_path))
    account = str(uuid.uuid4())
    cid = str(uuid.uuid4())
    db.create_account("unique-hash", "email", "hint", 0, account_id=account)
    sync = SyncStore(db)
    sync.set_enabled(account, True)
    sync.push(account, "test", [conv(cid)], [])
    store = ConversationFiles(db, SECRET)
    content = b"VERY-PRIVATE-CONTENT-TEST-29302903"
    meta = store.put(account, cid, "operation_restart_123", "file.txt", "text/plain", content)
    db.close()
    assert content not in db_path.read_bytes()
    db = Database(str(db_path))
    newer = "Zz9876543210YyXxWwVvUuTtSsRrQqPpOoNnMmLlKkJjIi"
    store = ConversationFiles(db, newer, previous_secrets=(SECRET,))
    assert store.get(account, cid, meta["file_id"])[1] == content
    db.close()


@pytest.mark.asyncio
async def test_feed_merge_keeps_referenced_bytes_in_new_conversation():
    app, client, cloud, store, a, b, cid, canonical = await setup_app()
    account = a["account"]["id"]
    private = store.put(account, cid, "operation_feed_123", "feed.txt", "text/plain", b"feed content")
    other_account = store.put(b["account"]["id"], cid, "operation_feed_123", "own.txt", "text/plain", b"other")
    mid = str(uuid.uuid4())
    result = app.state.sync.push(account, "test", [], [{"mid": mid, "cid": cid, "role": "user", "text": "message", "attachments": [{"file_id": private["file_id"]}]}])
    assert result["accepted"] == 1
    with cloud.db.tx() as c:
        c.execute("UPDATE sync_conversations SET title='Feed · 1' WHERE account_id=? AND cid=?", (account, cid))
        c.execute("UPDATE sync_conversations SET title='Лента' WHERE account_id=? AND cid=?", (account, canonical))
    assert app.state.sync.fold_all_feeds() == 1
    assert store.get(account, canonical, private["file_id"])[1] == b"feed content"
    assert store.get(b["account"]["id"], cid, other_account["file_id"])[1] == b"other"
    with cloud.db.tx() as c:
        assert c.execute("SELECT cid FROM sync_messages WHERE account_id=? AND mid=?", (account, mid)).fetchone()[0] == canonical
    await client.aclose()


@pytest.mark.asyncio
async def test_sync_only_accepts_files_from_same_conversation_and_sends_verified_metadata():
    _app, client, cloud, store, a, b, cid, other = await setup_app()
    first = store.put(a["account"]["id"], cid, "operation_sync_123", "correct.txt", "text/plain", b"content")
    other_key, _ = cloud._issue_key(a["account"]["id"], "second-device", "code")
    mid = str(uuid.uuid4())
    message = {"mid": mid, "cid": cid, "role": "user", "text": "test", "attachments": [{"file_id": first["file_id"], "name": "forged", "size": 9999}]}
    path = "/v1/sync/changes"
    sent = await client.post(path, headers=auth(a["api_key"]), json={"device": "first", "messages": [message]})
    assert sent.json()["accepted"] == 1
    rows = (await client.get(path + "?since=0", headers=auth(other_key))).json()["messages"]
    attachment = next(row for row in rows if row["mid"] == mid)["attachments"][0]
    assert attachment == {"file_id": first["file_id"], "name": "correct.txt", "mime": "text/plain", "size": 7}
    got = await client.get(f"/v1/sync/conversations/{cid}/files/{attachment['file_id']}", headers=auth(other_key))
    assert got.content == b"content"
    for key, target in ((a["api_key"], other), (b["api_key"], cid)):
        wrong = dict(message, mid=str(uuid.uuid4()), cid=target)
        result = await client.post(path, headers=auth(key), json={"device": "first", "messages": [wrong]})
        assert result.json()["rejected"][0]["reason"] == "attachment_unavailable"
    await client.aclose()


@pytest.mark.asyncio
async def test_quota_and_ciphertext_tampering_fail_without_losing_existing_data():
    _app, client, cloud, _store, a, _b, cid, _other = await setup_app()
    store = ConversationFiles(cloud.db, SECRET, max_account_bytes=5)
    account = a["account"]["id"]
    first = store.put(account, cid, "operation_quota_123", "a.txt", "text/plain", b"12345")
    with pytest.raises(PrivateFileError) as exc:
        store.put(account, cid, "operation_quota_456", "b.txt", "text/plain", b"x")
    assert getattr(exc.value, "code", "") == "storage_full"
    assert store.get(account, cid, first["file_id"])[1] == b"12345"
    with cloud.db.tx() as c:
        c.execute("UPDATE sync_private_files SET ciphertext=? WHERE file_id=?", (b"modified", first["file_id"]))
    with pytest.raises(PrivateFileError) as exc:
        store.get(account, cid, first["file_id"])
    assert getattr(exc.value, "code", "") == "storage_unavailable"
    await client.aclose()


@pytest.mark.asyncio
async def test_real_sync_client_reads_private_file_on_another_device():
    app, client, cloud, _store, a, b, cid, _other = await setup_app()
    second_key, _ = cloud._issue_key(a["account"]["id"], "second-device", "code")
    clients = [CloudClient("http://cloud.test", key) for key in (a["api_key"], second_key, b["api_key"])]
    urls = []
    async def capture(request):
        urls.append(str(request.url))
    for device in clients:
        await device._client.aclose()
        device._client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), event_hooks={"request": [capture]})
    one, two, stranger = clients
    sender, receiver = SyncClient(one), SyncClient(two)
    source, destination, outsider = (PrivateSyncFiles(device) for device in clients)
    original = b"A private file from one device\x00\xff"
    metadata = await source.upload(cid, "device_operation_123", "private.bin", "application/octet-stream", original)
    assert (await source.upload(cid, "device_operation_123", "private.bin", "application/octet-stream", original))["file_id"] == metadata["file_id"]
    mid = str(uuid.uuid4())
    result = await sender.push("first-device", [], [{"cid": cid, "mid": mid, "role": "user", "text": "file", "attachments": [{"file_id": metadata["file_id"]}]}])
    assert result["accepted"] == 1
    rows = (await receiver.changes())["messages"]
    received = next(row for row in rows if row["mid"] == mid)["attachments"][0]
    assert await destination.download(cid, received["file_id"], received["size"]) == original
    with pytest.raises(ClientCloudError) as exc:
        await outsider.download(cid, received["file_id"], received["size"])
    assert exc.value.status == 404
    assert all("private.bin" not in url for url in urls)
    for device in clients:
        await device.close()
    await client.aclose()


@pytest.mark.asyncio
async def test_wipe_generation_survives_later_off_on_and_preserves_other_account():
    app, client, cloud, store, a, b, cid, _ = await setup_app()
    account, other = a['account']['id'], b['account']['id']
    original = store.put(account, cid, 'wipe_operation_123', 'x.txt', 'text/plain', b'private')
    before = app.state.sync.state(account)
    assert before['private_generation'] == before['private_last_wipe_generation'] == 0
    app.state.sync.wipe(account)
    wipe = app.state.sync.state(account)
    assert (wipe['private_generation'], wipe['private_reset_reason'], wipe['private_last_wipe_generation']) == (1, 'wipe', 1)
    app.state.sync.set_enabled(account, False)
    app.state.sync.set_enabled(account, True)
    state = app.state.sync.state(account)
    assert (state['private_generation'], state['private_reset_reason'], state['private_last_wipe_generation']) == (2, 'sync_off', 1)
    assert app.state.sync.state(other)['private_generation'] == 0
    with pytest.raises(PrivateFileError):
        store.get(account, cid, original['file_id'])
    await client.aclose()


@pytest.mark.asyncio
async def test_deleting_last_reference_deletes_blob_but_other_references_keep_it():
    app, client, cloud, store, a, _, cid, _ = await setup_app()
    account = a['account']['id']
    file = store.put(account, cid, 'reference_operation_123', 'x.txt', 'text/plain', b'private')
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    for mid in (first, second):
        msg = {'mid': mid, 'cid': cid, 'role': 'user', 'text': 'x', 'attachments': [{'file_id': file['file_id']}]}
        assert app.state.sync.push(account, 'test', [], [msg])['accepted'] == 1
    assert app.state.sync.push(account, 'test', [], [{'mid': first, 'cid': cid, 'role': 'user', 'deleted': True}])['accepted'] == 1
    assert store.get(account, cid, file['file_id'])[1] == b'private'
    assert app.state.sync.push(account, 'test', [], [{'mid': second, 'cid': cid, 'role': 'user', 'deleted': True}])['accepted'] == 1
    with pytest.raises(PrivateFileError) as exc:
        store.get(account, cid, file['file_id'])
    assert exc.value.status == 404
    with pytest.raises(PrivateFileError) as exc:
        store.put(account, cid, 'reference_operation_123', 'x.txt', 'text/plain', b'private')
    assert exc.value.status == 409
    await client.aclose()


@pytest.mark.asyncio
async def test_explicit_file_deletion_with_live_reference_is_safe():
    app, client, _, store, a, _, cid, _ = await setup_app()
    account = a["account"]["id"]
    file = store.put(account, cid, "explicit_delete_123456", "x.txt", "text/plain", b"private")
    mid = str(uuid.uuid4())
    app.state.sync.push(account, "test", [], [{"mid": mid, "cid": cid, "role": "user", "text": "x", "attachments": [{"file_id": file["file_id"]}]}])
    store.delete(account, cid, file["file_id"])
    with pytest.raises(PrivateFileError) as exc:
        store.get(account, cid, file["file_id"])
    assert exc.value.status == 404
    await client.aclose()


@pytest.mark.asyncio
async def test_feed_fold_waits_for_unattached_upload_then_moves_file_and_message():
    app, client, cloud, store, a, _, cid, canonical = await setup_app()
    account = a['account']['id']
    with cloud.db.tx() as c:
        c.execute("UPDATE sync_conversations SET title='Feed · 1' WHERE account_id=? AND cid=?", (account, cid))
        c.execute("UPDATE sync_conversations SET title='Лента' WHERE account_id=? AND cid=?", (account, canonical))
    file = store.put(account, cid, 'feed_pending_123456', 'x.txt', 'text/plain', b'pending')
    assert app.state.sync.fold_all_feeds() == 0
    assert store.get(account, cid, file['file_id'])[1] == b'pending'
    mid = str(uuid.uuid4())
    assert app.state.sync.push(account, 'test', [], [{'mid': mid, 'cid': cid, 'role': 'user', 'text': 'x', 'attachments': [{'file_id': file['file_id']}]}])['accepted'] == 1
    assert store.get(account, canonical, file['file_id'])[1] == b'pending'
    with cloud.db.tx() as c:
        assert c.execute('SELECT cid FROM sync_messages WHERE account_id=? AND mid=?', (account, mid)).fetchone()[0] == canonical
    await client.aclose()


@pytest.mark.asyncio
async def test_private_download_limit_stays_held_after_cancellation():
    import threading

    app, client, cloud, store, a, _, cid, _ = await setup_app()
    file = store.put(a['account']['id'], cid, 'downloads_operation_123', 'x.txt', 'text/plain', b'private')
    path = f"/v1/sync/conversations/{cid}/files/{file['file_id']}"
    entered, release, lock = threading.Event(), threading.Event(), threading.Lock()
    count = 0
    real_get = store.get
    def blocked_get(*args):
        nonlocal count
        with lock:
            count += 1
            if count == 8:
                entered.set()
        if not release.wait(10):
            raise RuntimeError('test timeout')
        return real_get(*args)
    store.get = blocked_get
    tasks = [asyncio.create_task(client.get(path, headers=auth(a['api_key']))) for _ in range(8)]
    try:
        for _ in range(200):
            if entered.is_set():
                break
            await asyncio.sleep(0.025)
        assert entered.is_set(), count
        assert (await client.get(path, headers=auth(a['api_key']))).status_code == 503
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        assert (await client.get(path, headers=auth(a['api_key']))).status_code == 503
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    await client.aclose()


@pytest.mark.asyncio
async def test_private_download_limit_includes_slow_response_bodies():
    app, client, cloud, store, a, _, cid, _ = await setup_app()
    file = store.put(a['account']['id'], cid, 'slow_response_operation_123', 'x.txt', 'text/plain', b'private')
    path = f"/v1/sync/conversations/{cid}/files/{file['file_id']}"
    blocked, release = asyncio.Event(), asyncio.Event()
    count = 0
    async def request():
        nonlocal count
        delivered_request = False
        async def receive():
            nonlocal delivered_request
            if not delivered_request:
                delivered_request = True
                return {'type': 'http.request', 'body': b'', 'more_body': False}
            await release.wait()
            return {'type': 'http.disconnect'}
        async def send(message):
            nonlocal count
            if message['type'] == 'http.response.body':
                count += 1
                if count == 8:
                    blocked.set()
                await release.wait()
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': 'GET',
                 'scheme': 'http', 'path': path, 'raw_path': path.encode(), 'query_string': b'',
                 'headers': [(b'authorization', f"Bearer {a['api_key']}".encode())],
                 'client': ('127.0.0.1', 1), 'server': ('cloud.test', 80), 'root_path': ''}
        await app(scope, receive, send)
    tasks = [asyncio.create_task(request()) for _ in range(8)]
    try:
        await asyncio.wait_for(blocked.wait(), timeout=5)
        assert (await client.get(path, headers=auth(a['api_key']))).status_code == 503
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("same_conversation", [True, False])
async def test_pruned_file_is_preserved_and_recoverable_without_changing_old_retention(monkeypatch, same_conversation):
    from nanomuse_cloud.sync import LIMITS

    app, client, cloud, store, a, b, cid, other = await setup_app()
    account = a['account']['id']
    akey, bkey = a['api_key'], b['api_key']
    monkeypatch.setitem(LIMITS, 'messages', 1)
    data = b'unique private bytes remain after auto pruning'
    f = store.put(account, cid, 'prune_recovery_operation_123', 'private.txt', 'text/plain', data)
    mid = str(uuid.uuid4())
    assert app.state.sync.push(account, 'test', [], [{'mid': mid, 'cid': cid, 'role': 'user', 'text': 'first', 'attachments': [{'file_id': f['file_id']}]}])['accepted'] == 1
    newer = str(uuid.uuid4())
    result = app.state.sync.push(account, 'test', [], [{'mid': newer, 'cid': cid if same_conversation else other, 'role': 'user', 'text': 'newer'}])
    assert result['pruned']['messages'] == 1
    assert store.get(account, cid, f['file_id'])[1] == data
    recovery = await client.get('/v1/sync/files/recovery', headers=auth(akey))
    assert recovery.status_code == 200 and recovery.headers['cache-control'] == 'no-store'
    assert [(x['file_id'], x['cid'], x['recovery_reason']) for x in recovery.json()['files']] == [(f['file_id'], cid, 'history_pruned')]
    assert (await client.get('/v1/sync/files/recovery', headers=auth(bkey))).json()['files'] == []
    assert (await client.get('/v1/sync/files/recovery')).status_code == 401
    with cloud.db.tx() as c:
        assert c.execute('SELECT COUNT(*) FROM sync_private_prune_guard').fetchone()[0] == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_recovery_lists_unattached_with_pagination_and_allows_explicit_cleanup():
    app, client, cloud, store, a, b, cid, canonical = await setup_app()
    account, key = a['account']['id'], a['api_key']
    with cloud.db.tx() as c:
        c.execute("UPDATE sync_conversations SET title='Feed · 1' WHERE account_id=? AND cid=?", (account, cid))
        c.execute("UPDATE sync_conversations SET title='Лента' WHERE account_id=? AND cid=?", (account, canonical))
    ids = []
    for i in range(3):
        item = store.put(account, cid, f'orphan_operation_{i:04}', f'f{i}.txt', 'text/plain', f'{i}'.encode())
        ids.append(item['file_id'])
    assert app.state.sync.fold_all_feeds() == 0
    rows, cursor = [], ''
    while True:
        page = await client.get('/v1/sync/files/recovery', params={'limit': 1, **({'after': cursor} if cursor else {})}, headers=auth(key))
        assert page.status_code == 200
        result = page.json()
        rows.extend(result['files'])
        if not result['more']:
            break
        cursor = result['next']
    assert {x['file_id'] for x in rows} == set(ids)
    assert {x['recovery_reason'] for x in rows} == {'unattached'}
    assert (await client.get('/v1/sync/files/recovery', headers=auth(b['api_key']))).json()['files'] == []
    assert (await client.get('/v1/sync/files/recovery?limit=101', headers=auth(key))).status_code == 400
    for file_id in ids[1:]:
        path = f'/v1/sync/conversations/{cid}/files/{file_id}'
        assert (await client.delete(path, headers=auth(key))).status_code == 204
    mid = str(uuid.uuid4())
    attached = app.state.sync.push(account, 'test', [], [{'mid': mid, 'cid': cid, 'role': 'user', 'text': 'recovered', 'attachments': [{'file_id': ids[0]}]}])
    assert attached['accepted'] == 1
    assert (await client.get('/v1/sync/files/recovery', headers=auth(key))).json()['files'] == []
    app.state.sync.fold_all_feeds()
    with cloud.db.tx() as c:
        assert c.execute('SELECT deleted FROM sync_conversations WHERE account_id=? AND cid=?', (account, cid)).fetchone()[0] == 1
    assert store.get(account, canonical, ids[0])[1] == b'0'
    await client.aclose()


@pytest.mark.asyncio
async def test_pruned_reference_hold_survives_other_reference_deletion_and_requires_confirmation(monkeypatch):
    from nanomuse_cloud.sync import LIMITS

    app, client, cloud, store, a, b, cid, _ = await setup_app()
    account, key = a['account']['id'], a['api_key']
    monkeypatch.setitem(LIMITS, 'messages', 1)
    f = store.put(account, cid, 'shared_prune_operation_123', 'x.txt', 'text/plain', b'private')
    first, second, third = (str(uuid.uuid4()) for _ in range(3))
    def attached(mid):
        return {'mid': mid, 'cid': cid, 'role': 'user', 'text': 'x', 'attachments': [{'file_id': f['file_id']}]}
    for mid in (first, second):
        assert app.state.sync.push(account, 'test', [], [attached(mid)])['accepted'] == 1
    rows = (await client.get('/v1/sync/files/recovery', headers=auth(key))).json()['files']
    assert [(item['file_id'], item['recovery_count']) for item in rows] == [(f['file_id'], 1)]
    assert app.state.sync.push(account, 'test', [], [{'mid': second, 'cid': cid, 'role': 'user', 'deleted': True}])['accepted'] == 1
    assert store.get(account, cid, f['file_id'])[1] == b'private'
    url = f"/v1/sync/conversations/{cid}/files/{f['file_id']}/recovery/resolve"
    assert (await client.post(url, headers=auth(key))).status_code == 409
    assert app.state.sync.push(account, 'test', [], [attached(third)])['accepted'] == 1
    assert (await client.get('/v1/sync/files/recovery', headers=auth(key))).json()['files'][0]['recovery_count'] == 1
    assert (await client.post(url, headers=auth(b['api_key']))).status_code == 404
    assert (await client.post(url, headers=auth(key))).status_code == 204
    assert (await client.get('/v1/sync/files/recovery', headers=auth(key))).json()['files'] == []
    assert app.state.sync.push(account, 'test', [], [{'mid': third, 'cid': cid, 'role': 'user', 'deleted': True}])['accepted'] == 1
    with pytest.raises(PrivateFileError):
        store.get(account, cid, f['file_id'])
    await client.aclose()


@pytest.mark.asyncio
async def test_multiple_pruned_references_keep_independent_holds_even_across_reopen(monkeypatch):
    from nanomuse_cloud.sync import LIMITS

    app, client, cloud, store, a, _, cid, _ = await setup_app()
    account = a['account']['id']
    monkeypatch.setitem(LIMITS, 'messages', 1)
    f = store.put(account, cid, 'many_prune_operation_123', 'x.txt', 'text/plain', b'private')
    mids = [str(uuid.uuid4()) for _ in range(3)]
    for mid in mids:
        assert app.state.sync.push(account, 'test', [], [{'mid': mid, 'cid': cid, 'role': 'user', 'text': 'x', 'attachments': [{'file_id': f['file_id']}]}])['accepted'] == 1
    reopened = ConversationFiles(cloud.db, SECRET)
    assert reopened.recovery(account)['files'][0]['recovery_count'] == 2
    assert app.state.sync.push(account, 'test', [], [{'mid': mids[-1], 'cid': cid, 'role': 'user', 'deleted': True}])['accepted'] == 1
    assert reopened.get(account, cid, f['file_id'])[1] == b'private'
    assert reopened.recovery(account)['files'][0]['recovery_count'] == 2
    reopened.delete(account, cid, f['file_id'])
    assert reopened.recovery(account)['files'] == []
    await client.aclose()
