"""The private file door checks the current account on every request."""

from __future__ import annotations

import asyncio
import multiprocessing
import sqlite3
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Header, HTTPException

from nanomuse_cloud.private_attachments import PrivateAttachments, PrivateFileError, install_private_attachments

A = str(uuid.UUID("11111111-1111-4111-8111-111111111111"))
B = str(uuid.UUID("22222222-2222-4222-8222-222222222222"))
TEST_SECRET = "Aa0123456789BbCcDdEeFfGgHhIiJjKkLlMmNnOoPpQqRrSs"
OLD_SECRET = "old0123456789BbCcDdEeFfGgHhIiJjKkLlMmNnOoPpQqRrSs"
NEW_SECRET = "new0123456789BbCcDdEeFfGgHhIiJjKkLlMmNnOoPpQqRrSs"


def make_app(tmp_path, *, clock=lambda: 1000, uploads=4, **limits):
    store = PrivateAttachments(tmp_path / "private", TEST_SECRET, clock=clock, **limits)
    async def caller(authorization: str = Header(default="")):
        if authorization == "Bearer session-a":
            return SimpleNamespace(account_id=A, via="code")
        if authorization == "Bearer session-b":
            return SimpleNamespace(account_id=B, via="telegram")
        if authorization == "Bearer personal-key":
            return SimpleNamespace(account_id=A, via="user")
        if authorization == "Bearer no-account":
            return SimpleNamespace(account_id="", via="code")
        raise HTTPException(401, "sign in")
    app = FastAPI()
    install_private_attachments(app, store, caller, max_concurrent_uploads=uploads)
    return store, app


def headers(token="session-a", key="operation_123456789"):
    return {"Authorization": "Bearer " + token, "Idempotency-Key": key, "Content-Type": "text/plain"}


@pytest.mark.asyncio
async def test_two_accounts_private_auth_and_restart(tmp_path):
    store, app = make_app(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://relay") as client:
        uploaded = await client.post("/v1/private-attachments", params={"name": "секрет.txt"}, headers=headers(), content=b"private text A")
        assert uploaded.status_code == 200
        meta = uploaded.json()
        ident = meta["file_id"]
        assert meta["expires_at"] is None and meta["size"] == 14
        path = "/v1/private-attachments/" + ident
        own = await client.get(path, headers=headers())
        assert own.status_code == 200 and own.content == b"private text A"
        assert own.headers["cache-control"] == "no-store"
        assert own.headers["x-content-type-options"] == "nosniff"
        assert own.headers["content-type"] == "application/octet-stream"
        assert (await client.get(path, headers=headers("session-b"))).status_code == 404
        assert (await client.get(path)).status_code == 401
        assert (await client.get(path, headers=headers("personal-key"))).status_code == 403
        assert (await client.get(path, headers=headers("no-account"))).status_code == 401
        assert (await client.delete(path, headers=headers("session-b"))).status_code == 404
        again = await client.post("/v1/private-attachments", params={"name": "секрет.txt"}, headers=headers(), content=b"private text A")
        assert again.json() == meta
        conflict = await client.post("/v1/private-attachments", params={"name": "секрет.txt"}, headers=headers(), content=b"changed")
        assert conflict.status_code == 409
    # No contents or bearer tokens appear as plaintext in the local database.
    raw = store.path.read_bytes()
    assert b"private text A" not in raw and b"session-a" not in raw
    restarted, app = make_app(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://relay") as client:
        assert (await client.get(path, headers=headers())).content == b"private text A"
        assert (await client.delete(path, headers=headers())).status_code == 204
        assert (await client.get(path, headers=headers())).status_code == 404
        assert (await client.post("/v1/private-attachments", params={"name": "секрет.txt"}, headers=headers(), content=b"private text A")).status_code == 409
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.root.stat().st_mode & 0o777 == 0o700
    assert restarted.path == store.path


@pytest.mark.asyncio
async def test_expiry_purge_and_no_cross_account_delete(tmp_path):
    now = [1000]
    store, app = make_app(tmp_path, clock=lambda: now[0], ttl_seconds=5)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://relay") as client:
        a = (await client.post("/v1/private-attachments", params={"name": "a.txt"}, headers=headers(), content=b"a")).json()
        b = (await client.post("/v1/private-attachments", params={"name": "b.txt"}, headers=headers("session-b"), content=b"b")).json()
        store.purge_account(A)
        assert (await client.get("/v1/private-attachments/" + a["file_id"], headers=headers())).status_code == 404
        assert (await client.get("/v1/private-attachments/" + b["file_id"], headers=headers("session-b"))).content == b"b"
        now[0] = 1005
        assert (await client.get("/v1/private-attachments/" + b["file_id"], headers=headers("session-b"))).status_code == 404
        assert store.cleanup_expired() == 1
        assert (await client.post("/v1/private-attachments", params={"name": "b.txt"}, headers=headers("session-b"), content=b"b")).status_code == 409


@pytest.mark.asyncio
async def test_limits_streaming_idempotency_and_concurrency(tmp_path):
    store, app = make_app(tmp_path, max_file_bytes=4, max_account_bytes=6, max_total_bytes=10, max_account_rows=5)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://relay") as client:
        async def stream():
            yield b"123"
            yield b"45"
        assert (await client.post("/v1/private-attachments", params={"name": "a"}, headers=headers(), content=stream())).status_code == 413
        assert (await client.post("/v1/private-attachments", params={"name": "../bad"}, headers=headers(), content=b"x")).status_code == 400
        assert (await client.post("/v1/private-attachments", params={"name": "a"}, headers={**headers(), "Idempotency-Key": "short"}, content=b"x")).status_code == 400
        async def upload(key):
            return await client.post("/v1/private-attachments", params={"name": "a"}, headers=headers(key=key), content=b"1234")
        results = await asyncio.gather(upload("operation_000000001"), upload("operation_000000002"))
        assert sorted(response.status_code for response in results) == [200, 507]
        rejected = next(response for response in results if response.status_code == 507)
        assert rejected.json()["error"]["code"] == "storage_full" and "Удалите" in rejected.json()["error"]["message"]
        assert len(store.get(A, next(response.json()["file_id"] for response in results if response.status_code == 200))[1]) == 4
        assert (await client.post("/v1/private-attachments", params={"name": "b"}, headers=headers("session-b"), content=b"1234")).status_code == 200


@pytest.mark.asyncio
async def test_upload_admission_does_not_read_body_and_releases_on_cancel_or_error(tmp_path):
    _store, app = make_app(tmp_path, uploads=1, max_file_bytes=4)
    started, release = asyncio.Event(), asyncio.Event()
    rejected_body_read = False

    async def blocked_body():
        started.set()
        await release.wait()
        yield b"data"

    async def should_not_be_read():
        nonlocal rejected_body_read
        rejected_body_read = True
        yield b"data"

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://relay") as client:
        pending = asyncio.create_task(client.post("/v1/private-attachments", params={"name": "a"}, headers=headers(), content=blocked_body()))
        await asyncio.wait_for(started.wait(), 5)
        denied = await client.post("/v1/private-attachments", params={"name": "b"}, headers=headers(key="operation_000000002"), content=should_not_be_read())
        assert denied.status_code == 503 and denied.json()["error"]["code"] == "upload_busy"
        assert not rejected_body_read
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        oversized = await client.post("/v1/private-attachments", params={"name": "b"}, headers=headers(key="operation_000000002"), content=b"12345")
        assert oversized.status_code == 413
        accepted = await client.post("/v1/private-attachments", params={"name": "b"}, headers=headers(key="operation_000000002"), content=b"ok")
        assert accepted.status_code == 200


@pytest.mark.asyncio
async def test_cancelled_request_holds_slot_until_background_write_finishes(tmp_path):
    store, app = make_app(tmp_path, uploads=1)
    started, release = threading.Event(), threading.Event()
    original_put = store.put

    def slow_put(*args):
        started.set()
        if not release.wait(10):
            raise TimeoutError("writer did not resume")
        return original_put(*args)

    store.put = slow_put
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://relay") as client:
        pending = asyncio.create_task(client.post("/v1/private-attachments", params={"name": "a"}, headers=headers(), content=b"a"))
        assert await asyncio.to_thread(started.wait, 5)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        denied = await client.post("/v1/private-attachments", params={"name": "b"}, headers=headers(key="operation_000000002"), content=b"b")
        assert denied.status_code == 503
        release.set()
        # Observe the underlying task's completion before checking readmission.
        async with asyncio.timeout(5):
            while True:
                retry = await client.post("/v1/private-attachments", params={"name": "b"}, headers=headers(key="operation_000000002"), content=b"b")
                if retry.status_code != 503:
                    break
                await asyncio.sleep(0.01)
        assert retry.status_code == 200


def test_swapped_or_corrupt_ciphertext_fails_closed(tmp_path):
    store, _app = make_app(tmp_path)
    a = store.put(A, "operation_000000001", "a", "text/plain", b"sensitive A")
    b = store.put(B, "operation_000000001", "b", "text/plain", b"sensitive B")
    with sqlite3.connect(store.path) as db:
        nonce, content = db.execute("SELECT nonce,ciphertext FROM private_attachments WHERE file_id=?", (b["file_id"],)).fetchone()
        db.execute("UPDATE private_attachments SET nonce=?,ciphertext=? WHERE file_id=?", (nonce, content, a["file_id"]))
    with pytest.raises(PrivateFileError) as error:
        store.get(A, a["file_id"])
    assert error.value.status == 503
    assert store.get(B, b["file_id"])[1] == b"sensitive B"


def test_secret_rotation_uses_only_named_previous_keys_and_never_deletes_data(tmp_path):
    path = tmp_path / "storage"
    old = PrivateAttachments(path, OLD_SECRET, clock=lambda: 1000)
    file = old.put(A, "operation_000000001", "file.txt", "text/plain", b"old data")
    without_key = PrivateAttachments(path, NEW_SECRET, clock=lambda: 1000)
    with pytest.raises(PrivateFileError) as error:
        without_key.get(A, file["file_id"])
    assert error.value.status == 503
    rotated = PrivateAttachments(path, NEW_SECRET, previous_secrets=(OLD_SECRET,), clock=lambda: 1000)
    assert rotated.get(A, file["file_id"])[1] == b"old data"
    fresh = rotated.put(A, "operation_000000002", "file.txt", "text/plain", b"new data")
    assert rotated.get(A, fresh["file_id"])[1] == b"new data"
    assert without_key.get(A, fresh["file_id"])[1] == b"new data"


def test_storage_rejects_symlink_and_insecure_existing_db(tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    link = tmp_path / "link"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        PrivateAttachments(link, TEST_SECRET)
    root = tmp_path / "store"
    root.mkdir()
    db = root / "private-attachments.sqlite3"
    db.write_bytes(b"not a database")
    db.chmod(0o644)
    with pytest.raises(ValueError):
        PrivateAttachments(root, TEST_SECRET)


def test_rejects_weak_or_missing_server_secrets_and_weak_previous_keys(tmp_path):
    for secret in ("", "short", "x" * 43):
        with pytest.raises(ValueError):
            PrivateAttachments(tmp_path / "store", secret)
    with pytest.raises(ValueError):
        PrivateAttachments(tmp_path / "store", TEST_SECRET, previous_secrets=("weak",))


@pytest.mark.asyncio
async def test_duplicate_concurrent_uploads_are_one_object(tmp_path):
    store, app = make_app(tmp_path, uploads=12)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://relay") as client:
        async def upload():
            return await client.post("/v1/private-attachments", params={"name": "a"}, headers=headers(), content=b"same")
        replies = await asyncio.gather(*(upload() for _ in range(12)))
    assert all(reply.status_code == 200 for reply in replies)
    assert len({reply.json()["file_id"] for reply in replies}) == 1
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM private_attachments").fetchone()[0] == 1


def test_files_do_not_expire_by_default_and_explicit_delete_clears_content(tmp_path):
    now = [1000]
    store, _app = make_app(tmp_path, clock=lambda: now[0])
    meta = store.put(A, "operation_000000001", "keep.txt", "text/plain", b"keep")
    assert meta["expires_at"] is None
    now[0] += 10 * 365 * 24 * 3600
    assert store.cleanup_expired() == 0
    assert store.get(A, meta["file_id"])[1] == b"keep"
    store.delete(A, meta["file_id"])
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM private_attachments").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM private_attachment_operations").fetchone()[0] == 1
    with pytest.raises(PrivateFileError) as error:
        store.put(A, "operation_000000001", "keep.txt", "text/plain", b"keep")
    assert error.value.status == 409
    now[0] += 100 * 365 * 24 * 3600
    assert store.cleanup_expired() == 0
    with pytest.raises(PrivateFileError) as error:
        store.put(A, "operation_000000001", "keep.txt", "text/plain", b"keep")
    assert error.value.status == 409


def test_operation_quota_reserves_space_for_deletion_and_is_account_scoped(tmp_path):
    store, _app = make_app(tmp_path, max_account_operations=1, max_total_operations=2)
    first = store.put(A, "operation_000000001", "a", "text/plain", b"a")
    # Deletion always succeeds even at the operation limit.
    store.delete(A, first["file_id"])
    with pytest.raises(PrivateFileError) as error:
        store.put(A, "operation_000000002", "b", "text/plain", b"b")
    assert error.value.code == "operation_history_full"
    store.put(B, "operation_000000002", "b", "text/plain", b"b")
    other = str(uuid.uuid4())
    with pytest.raises(PrivateFileError) as error:
        store.put(other, "operation_000000003", "c", "text/plain", b"c")
    assert error.value.code == "operation_history_full"


def _account_deletion_race(directory, action, barrier, results):
    store = PrivateAttachments(Path(directory), TEST_SECRET, clock=lambda: 1000)
    barrier.wait(timeout=20)
    try:
        if action == "put":
            store.put(A, "operation_000000001", "a", "text/plain", b"secret")
        else:
            store.purge_account(A)
        results.put((action, "ok"))
    except PrivateFileError as exc:
        results.put((action, exc.code))


def test_account_deletion_fences_late_uploads_in_either_order(tmp_path):
    for order in ("upload_first", "delete_first"):
        store, _app = make_app(tmp_path / order)
        if order == "upload_first":
            store.put(A, "operation_000000001", "a", "text/plain", b"secret")
        store.purge_account(A)
        with pytest.raises(PrivateFileError) as error:
            store.put(A, "operation_000000002", "a", "text/plain", b"secret")
        assert error.value.code == "account_deleted"
        with sqlite3.connect(store.path) as db:
            assert db.execute("SELECT COUNT(*) FROM private_attachments WHERE account_id=?", (A,)).fetchone()[0] == 0
        assert store.put(B, "operation_000000003", "b", "text/plain", b"other")["size"] == 5


def test_account_delete_and_upload_from_two_processes_never_leave_data(tmp_path):
    store, _app = make_app(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    results = ctx.Queue()
    processes = [ctx.Process(target=_account_deletion_race, args=(str(store.root), action, barrier, results))
                 for action in ("put", "purge")]
    for process in processes:
        process.start()
    for process in processes:
        process.join(30)
        assert process.exitcode == 0
    outcomes = dict(results.get(timeout=5) for _ in processes)
    assert outcomes["purge"] == "ok"
    assert outcomes["put"] in {"ok", "account_deleted"}
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM private_attachments WHERE account_id=?", (A,)).fetchone()[0] == 0
    with pytest.raises(PrivateFileError) as error:
        store.put(A, "operation_000000001", "a", "text/plain", b"secret")
    assert error.value.code == "account_deleted"
