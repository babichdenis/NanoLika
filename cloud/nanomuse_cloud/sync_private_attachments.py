"""Private cloud copies bound transactionally to one account and conversation.

Not installed by default. The relay integrator must mount the routes with its normal
session dependency and ensure the client's original file stays in its own workspace.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Depends, Request
from fastapi.responses import JSONResponse, Response

from .private_attachments import (
    MAX_ACCOUNT_BYTES,
    MAX_ACCOUNT_OPERATIONS,
    MAX_ACCOUNT_ROWS,
    MAX_FILE_BYTES,
    MAX_TOTAL_BYTES,
    MAX_TOTAL_OPERATIONS,
    MAX_TOTAL_ROWS,
    PrivateFileError,
    _account,
    _file_id,
    _mime,
    _name,
    _valid_secret,
)
from .sync import valid_id

_OPERATION = re.compile(r"[A-Za-z0-9_-]{16,128}\Z")


class ConversationFiles:
    """Stored in the relay's DB so deletion and upload share one transaction."""

    def __init__(self, db: Any, secret: str, *, previous_secrets: tuple[str, ...] = (),
                 clock: Callable[[], float] = time.time, max_file_bytes: int = MAX_FILE_BYTES,
                 max_account_bytes: int = MAX_ACCOUNT_BYTES, max_total_bytes: int = MAX_TOTAL_BYTES,
                 max_account_rows: int = MAX_ACCOUNT_ROWS, max_total_rows: int = MAX_TOTAL_ROWS,
                 max_account_operations: int = MAX_ACCOUNT_OPERATIONS, max_total_operations: int = MAX_TOTAL_OPERATIONS):
        if not _valid_secret(secret) or any(not _valid_secret(value) for value in previous_secrets):
            raise ValueError("random server secrets are required")
        if min(max_file_bytes, max_account_bytes, max_total_bytes, max_account_rows, max_total_rows, max_account_operations, max_total_operations) <= 0:
            raise ValueError("positive limits are required")
        self.db, self.clock = db, clock
        self.max_file_bytes, self.max_account_bytes, self.max_total_bytes = max_file_bytes, max_account_bytes, max_total_bytes
        self.max_account_rows, self.max_total_rows = max_account_rows, max_total_rows
        self.max_account_operations, self.max_total_operations = max_account_operations, max_total_operations
        keys = [hmac.new(value.encode(), b"nanolika:sync-private-files:v1", hashlib.sha256).digest() for value in (secret, *previous_secrets)]
        self._keys = {hashlib.sha256(value).hexdigest()[:16]: value for value in keys}
        self._key_id = hashlib.sha256(keys[0]).hexdigest()[:16]
        with db.tx_durable() as c:
            c.execute("CREATE TABLE IF NOT EXISTS sync_private_schema_meta (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL)")
            version_row = c.execute("SELECT version FROM sync_private_schema_meta WHERE id=1").fetchone()
            version = int(version_row[0]) if version_row else 0
            if version > 1:
                raise RuntimeError("private file schema is newer than this code")
            # A writer lock spans the entire upgrade, including replacement of old
            # triggers. Other relay processes see either the old or the new schema.
            for name in (
                "sync_private_message_moved", "sync_private_file_referenced",
                "sync_private_ref_added", "sync_private_refs_updated",
                "sync_private_last_ref_deleted", "sync_private_conversation_deleted",
                "sync_private_conversation_tombstoned", "sync_private_sync_disabled",
                "sync_private_account_deleted",
            ):
                c.execute(f"DROP TRIGGER IF EXISTS {name}")
            c.execute("""CREATE TABLE IF NOT EXISTS sync_private_files (
                file_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, cid TEXT NOT NULL, aad_cid TEXT NOT NULL,
                operation TEXT NOT NULL, request_digest TEXT NOT NULL,
                name TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL,
                recovery_reason TEXT NOT NULL DEFAULT 'unattached',
                created INTEGER NOT NULL, key_id TEXT NOT NULL, nonce BLOB NOT NULL, ciphertext BLOB NOT NULL,
                UNIQUE(account_id,operation),
                FOREIGN KEY(account_id,cid) REFERENCES sync_conversations(account_id,cid) ON DELETE CASCADE)""")
            file_columns = {row["name"] for row in c.execute("PRAGMA table_info(sync_private_files)")}
            if "aad_cid" not in file_columns:
                # Older ciphertext authenticated the original conversation id.
                c.execute("ALTER TABLE sync_private_files ADD COLUMN aad_cid TEXT NOT NULL DEFAULT ''")
                c.execute("UPDATE sync_private_files SET aad_cid=cid")
            if "recovery_reason" not in file_columns:
                c.execute("ALTER TABLE sync_private_files ADD COLUMN recovery_reason TEXT NOT NULL DEFAULT 'unattached'")
            c.execute("CREATE INDEX IF NOT EXISTS sync_private_files_account_cid ON sync_private_files(account_id,cid)")
            # A feed merge can move messages to the canonical conversation. Rebind
            # referenced copies in the same transaction before the old conversation
            # is tombstoned. aad_cid remains immutable so existing ciphertext can be
            # authenticated without needing encryption keys inside a SQLite trigger.
            c.execute("""CREATE TRIGGER IF NOT EXISTS sync_private_message_moved
                AFTER UPDATE OF cid ON sync_messages WHEN OLD.cid!=NEW.cid BEGIN
                  UPDATE sync_private_files SET cid=NEW.cid
                    WHERE account_id=OLD.account_id AND cid=OLD.cid AND file_id IN (
                      SELECT json_extract(value,'$.file_id') FROM json_each(
                        CASE WHEN json_valid(OLD.attachments) THEN OLD.attachments ELSE '[]' END)
                      WHERE json_valid(value) AND json_type(value)='object');
                END""")
            c.execute("""CREATE TABLE IF NOT EXISTS sync_private_operations (
                account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                operation TEXT NOT NULL, PRIMARY KEY(account_id,operation))""")
            c.execute("""CREATE TABLE IF NOT EXISTS sync_private_generations (
                account_id TEXT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
                generation INTEGER NOT NULL CHECK(generation>=0), reason TEXT NOT NULL,
                last_wipe_generation INTEGER NOT NULL CHECK(last_wipe_generation>=0))""")
            generation_columns = {row["name"] for row in c.execute("PRAGMA table_info(sync_private_generations)")}
            if "reason" not in generation_columns:
                c.execute("ALTER TABLE sync_private_generations ADD COLUMN reason TEXT NOT NULL DEFAULT 'legacy_unknown'")
            if "last_wipe_generation" not in generation_columns:
                c.execute("ALTER TABLE sync_private_generations ADD COLUMN last_wipe_generation INTEGER NOT NULL DEFAULT 0 CHECK(last_wipe_generation>=0)")
                # Old generations do not record whether an explicit wipe occurred.
                # Conservatively prevent a client from restoring wiped history.
                c.execute("UPDATE sync_private_generations SET last_wipe_generation=generation")
            c.execute("""CREATE TABLE IF NOT EXISTS sync_private_file_refs (
                account_id TEXT NOT NULL, mid TEXT NOT NULL, file_id TEXT NOT NULL,
                PRIMARY KEY(account_id,mid,file_id),
                FOREIGN KEY(account_id,mid) REFERENCES sync_messages(account_id,mid) ON DELETE CASCADE,
                FOREIGN KEY(file_id) REFERENCES sync_private_files(file_id) ON DELETE CASCADE)""")
            c.execute("CREATE INDEX IF NOT EXISTS sync_private_refs_file ON sync_private_file_refs(file_id)")
            c.execute("""CREATE TABLE IF NOT EXISTS sync_private_prune_guard (
                account_id TEXT NOT NULL, mid TEXT NOT NULL, PRIMARY KEY(account_id,mid))""")
            c.execute("""CREATE TABLE IF NOT EXISTS sync_private_recovery_holds (
                file_id TEXT NOT NULL REFERENCES sync_private_files(file_id) ON DELETE CASCADE,
                account_id TEXT NOT NULL, mid TEXT NOT NULL, reason TEXT NOT NULL,
                PRIMARY KEY(file_id,mid,reason))""")
            # Preserve existing flagged copies if an experimental DB is reopened.
            c.execute("""INSERT OR IGNORE INTO sync_private_recovery_holds(file_id,account_id,mid,reason)
                SELECT f.file_id,f.account_id,'','history_pruned' FROM sync_private_files f
                WHERE f.recovery_reason='history_pruned' AND NOT EXISTS
                  (SELECT 1 FROM sync_private_recovery_holds h WHERE h.file_id=f.file_id)""")
            c.execute("""CREATE TRIGGER IF NOT EXISTS sync_private_file_referenced
                AFTER INSERT ON sync_private_file_refs BEGIN
                  UPDATE sync_private_files SET recovery_reason='' WHERE file_id=NEW.file_id;
                END""")
            c.execute("""CREATE TRIGGER IF NOT EXISTS sync_private_ref_added
                AFTER INSERT ON sync_messages WHEN NEW.deleted=0 BEGIN
                  INSERT OR IGNORE INTO sync_private_file_refs(account_id,mid,file_id)
                  SELECT NEW.account_id,NEW.mid,f.file_id FROM sync_private_files f
                  JOIN json_each(CASE WHEN json_valid(NEW.attachments) THEN NEW.attachments ELSE '[]' END) j
                    ON j.type='object' AND f.file_id=json_extract(j.value,'$.file_id')
                  WHERE f.account_id=NEW.account_id AND f.cid=NEW.cid;
                END""")
            c.execute("""CREATE TRIGGER IF NOT EXISTS sync_private_refs_updated
                AFTER UPDATE OF attachments,deleted ON sync_messages BEGIN
                  DELETE FROM sync_private_file_refs WHERE account_id=NEW.account_id AND mid=NEW.mid
                    AND (NEW.deleted=1 OR file_id NOT IN (
                      SELECT json_extract(value,'$.file_id') FROM json_each(
                        CASE WHEN json_valid(NEW.attachments) THEN NEW.attachments ELSE '[]' END)
                      WHERE type='object' AND json_extract(value,'$.file_id') IS NOT NULL));
                  INSERT OR IGNORE INTO sync_private_file_refs(account_id,mid,file_id)
                  SELECT NEW.account_id,NEW.mid,f.file_id FROM sync_private_files f
                  JOIN json_each(CASE WHEN json_valid(NEW.attachments) THEN NEW.attachments ELSE '[]' END) j
                    ON j.type='object' AND f.file_id=json_extract(j.value,'$.file_id')
                  WHERE NEW.deleted=0 AND f.account_id=NEW.account_id AND f.cid=NEW.cid;
                END""")
            c.execute("""CREATE TRIGGER IF NOT EXISTS sync_private_last_ref_deleted
                AFTER DELETE ON sync_private_file_refs
                WHEN NOT EXISTS (SELECT 1 FROM sync_private_file_refs WHERE file_id=OLD.file_id)
                  AND NOT EXISTS (SELECT 1 FROM sync_private_recovery_holds WHERE file_id=OLD.file_id)
                  AND NOT EXISTS (SELECT 1 FROM sync_private_prune_guard WHERE account_id=OLD.account_id AND mid=OLD.mid) BEGIN
                  INSERT OR IGNORE INTO sync_private_operations
                    SELECT account_id,operation FROM sync_private_files WHERE file_id=OLD.file_id;
                  DELETE FROM sync_private_files WHERE file_id=OLD.file_id;
                END""")
            # Earlier revisions did not maintain the reference index. Rebuild only
            # positive, same-account/same-conversation links; never discard a file.
            if version < 1:
                c.execute("""INSERT OR IGNORE INTO sync_private_file_refs(account_id,mid,file_id)
                    SELECT m.account_id,m.mid,f.file_id FROM sync_messages m
                    JOIN json_each(CASE WHEN json_valid(m.attachments) THEN m.attachments ELSE '[]' END) j
                    JOIN sync_private_files f ON f.account_id=m.account_id AND f.cid=m.cid
                      AND f.file_id=json_extract(CASE WHEN j.type='object' THEN j.value ELSE '{}' END,'$.file_id')
                    WHERE m.deleted=0""")
            # Explicit conversation deletion and sync wipe are atomic with the private
            # copies. Existing local workspaces are outside the relay DB and untouched.
            c.execute("""CREATE TRIGGER IF NOT EXISTS sync_private_conversation_deleted
                BEFORE DELETE ON sync_conversations BEGIN
                  INSERT OR IGNORE INTO sync_private_operations
                    SELECT account_id,operation FROM sync_private_files WHERE account_id=OLD.account_id AND cid=OLD.cid;
                  DELETE FROM sync_private_files WHERE account_id=OLD.account_id AND cid=OLD.cid;
                END""")
            c.execute("""CREATE TRIGGER IF NOT EXISTS sync_private_conversation_tombstoned
                AFTER UPDATE OF deleted ON sync_conversations WHEN NEW.deleted=1 AND OLD.deleted=0 BEGIN
                  INSERT OR IGNORE INTO sync_private_operations
                    SELECT account_id,operation FROM sync_private_files WHERE account_id=NEW.account_id AND cid=NEW.cid;
                  DELETE FROM sync_private_files WHERE account_id=NEW.account_id AND cid=NEW.cid;
                END""")
            c.execute("""CREATE TRIGGER IF NOT EXISTS sync_private_sync_disabled
                AFTER UPDATE OF sync_enabled ON accounts WHEN NEW.sync_enabled=0 AND OLD.sync_enabled!=0 BEGIN
                  INSERT OR IGNORE INTO sync_private_operations
                    SELECT account_id,operation FROM sync_private_files WHERE account_id=NEW.id;
                  DELETE FROM sync_private_files WHERE account_id=NEW.id;
                END""")
            c.execute("""CREATE TRIGGER IF NOT EXISTS sync_private_account_deleted
                BEFORE DELETE ON accounts BEGIN
                  DELETE FROM sync_private_files WHERE account_id=OLD.id;
                  DELETE FROM sync_private_operations WHERE account_id=OLD.id;
                END""")
            c.execute("INSERT INTO sync_private_schema_meta(id,version) VALUES(1,1) ON CONFLICT(id) DO UPDATE SET version=excluded.version")

    def _key(self, account: str, key_id: str) -> bytes:
        master = self._keys.get(key_id)
        if master is None:
            raise PrivateFileError(503, "storage_key_unavailable")
        return hmac.new(master, account.encode(), hashlib.sha256).digest()

    @staticmethod
    def _aad(account: str, cid: str, ident: str, name: str, mime: str, size: int) -> bytes:
        return json.dumps([account, cid, ident, name, mime, size], ensure_ascii=False, separators=(",", ":")).encode()

    @staticmethod
    def _conversation(c: Any, account: str, cid: str) -> None:
        row = c.execute("SELECT a.sync_enabled, s.deleted FROM accounts a JOIN sync_conversations s ON s.account_id=a.id WHERE a.id=? AND s.cid=?", (account, cid)).fetchone()
        if row is None or not row["sync_enabled"] or row["deleted"]:
            raise PrivateFileError(404, "conversation_not_found")

    @staticmethod
    def _cid(cid: str) -> str:
        valid = valid_id(cid)
        if valid is None or valid != cid:
            raise PrivateFileError(404, "conversation_not_found")
        return valid

    def put(self, account: str, cid: str, operation: str, name: str, mime: str, data: bytes) -> dict[str, Any]:
        account, cid, name, mime = _account(account), self._cid(cid), _name(name), _mime(mime)
        if not isinstance(operation, str) or not _OPERATION.fullmatch(operation):
            raise PrivateFileError(400, "invalid_idempotency_key")
        if not isinstance(data, bytes) or len(data) > self.max_file_bytes:
            raise PrivateFileError(413, "file_too_large")
        op = hashlib.sha256(operation.encode()).hexdigest()
        digest = hashlib.sha256(json.dumps([cid, name, mime, hashlib.sha256(data).hexdigest()], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        with self.db.tx_durable() as c:
            self._conversation(c, account, cid)
            if c.execute("SELECT 1 FROM sync_private_operations WHERE account_id=? AND operation=?", (account, op)).fetchone():
                raise PrivateFileError(409, "operation_conflict")
            old = c.execute("SELECT * FROM sync_private_files WHERE account_id=? AND operation=?", (account, op)).fetchone()
            if old:
                if old["request_digest"] != digest:
                    raise PrivateFileError(409, "operation_conflict")
                return self._metadata(old)
            used, count = c.execute("SELECT COALESCE(SUM(size),0),COUNT(*) FROM sync_private_files WHERE account_id=?", (account,)).fetchone()
            total, rows = c.execute("SELECT COALESCE(SUM(size),0),COUNT(*) FROM sync_private_files").fetchone()
            if used + len(data) > self.max_account_bytes or total + len(data) > self.max_total_bytes or count >= self.max_account_rows or rows >= self.max_total_rows:
                raise PrivateFileError(507, "storage_full")
            ops = c.execute("SELECT COUNT(*) FROM sync_private_operations WHERE account_id=?", (account,)).fetchone()[0]
            all_ops = c.execute("SELECT COUNT(*) FROM sync_private_operations").fetchone()[0]
            if count + ops >= self.max_account_operations or rows + all_ops >= self.max_total_operations:
                raise PrivateFileError(507, "operation_history_full")
            ident, created, nonce = str(uuid.uuid4()), int(self.clock()), os.urandom(12)
            encrypted = AESGCM(self._key(account, self._key_id)).encrypt(nonce, data, self._aad(account, cid, ident, name, mime, len(data)))
            c.execute("INSERT INTO sync_private_files(file_id,account_id,cid,aad_cid,operation,request_digest,name,mime,size,created,key_id,nonce,ciphertext) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (ident, account, cid, cid, op, digest, name, mime, len(data), created, self._key_id, nonce, encrypted))
            return self._metadata(c.execute("SELECT * FROM sync_private_files WHERE file_id=?", (ident,)).fetchone())

    @staticmethod
    def _metadata(row: Any) -> dict[str, Any]:
        return {"file_id": row["file_id"], "cid": row["cid"], "name": row["name"], "mime": row["mime"], "size": row["size"], "created_at": row["created"], "expires_at": None}

    def get(self, account: str, cid: str, ident: str) -> tuple[dict[str, Any], bytes]:
        account, cid, ident = _account(account), self._cid(cid), _file_id(ident)
        with self.db.tx() as c:
            self._conversation(c, account, cid)
            row = c.execute("SELECT * FROM sync_private_files WHERE account_id=? AND cid=? AND file_id=?", (account, cid, ident)).fetchone()
        if row is None:
            raise PrivateFileError(404, "not_found")
        try:
            data = AESGCM(self._key(account, row["key_id"])).decrypt(row["nonce"], row["ciphertext"], self._aad(account, row["aad_cid"], ident, row["name"], row["mime"], row["size"]))
        except (InvalidTag, TypeError, ValueError):
            raise PrivateFileError(503, "storage_unavailable") from None
        if len(data) != row["size"]:
            raise PrivateFileError(503, "storage_unavailable")
        return self._metadata(row), data

    def delete(self, account: str, cid: str, ident: str) -> None:
        account, cid, ident = _account(account), self._cid(cid), _file_id(ident)
        with self.db.tx_durable() as c:
            self._conversation(c, account, cid)
            row = c.execute("SELECT operation FROM sync_private_files WHERE account_id=? AND cid=? AND file_id=?", (account, cid, ident)).fetchone()
            if row is None:
                raise PrivateFileError(404, "not_found")
            c.execute("INSERT OR IGNORE INTO sync_private_operations VALUES (?,?)", (account, row["operation"]))
            c.execute("DELETE FROM sync_private_files WHERE account_id=? AND cid=? AND file_id=?", (account, cid, ident))

    def recovery(self, account: str, after: str = "", limit: int = 50) -> dict[str, Any]:
        """List unattached copies for explicit reattachment or deletion; never expire them."""
        account = _account(account)
        if after:
            after = _file_id(after)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise PrivateFileError(400, "invalid_limit")
        with self.db.tx() as c:
            state = c.execute("SELECT sync_enabled FROM accounts WHERE id=?", (account,)).fetchone()
            if state is None or not state[0]:
                raise PrivateFileError(409, "sync_off")
            rows = c.execute("""SELECT f.file_id,f.cid,f.name,f.mime,f.size,f.created,f.recovery_reason,
                (SELECT COUNT(*) FROM sync_private_recovery_holds h WHERE h.file_id=f.file_id) AS recovery_count
                FROM sync_private_files f
                JOIN sync_conversations sc ON sc.account_id=f.account_id AND sc.cid=f.cid AND sc.deleted=0
                WHERE f.account_id=? AND f.file_id>? AND (NOT EXISTS
                  (SELECT 1 FROM sync_private_file_refs r WHERE r.file_id=f.file_id) OR EXISTS
                  (SELECT 1 FROM sync_private_recovery_holds h WHERE h.file_id=f.file_id))
                ORDER BY f.file_id LIMIT ?""", (account, after, limit + 1)).fetchall()
        items = [{**self._metadata(row), "recovery_reason": "history_pruned" if row["recovery_count"] else (row["recovery_reason"] or "unattached"), "recovery_count": row["recovery_count"]} for row in rows[:limit]]
        return {"files": items, "more": len(rows) > limit, "next": items[-1]["file_id"] if len(rows) > limit else ""}

    def resolve_recovery(self, account: str, cid: str, ident: str) -> None:
        """Confirm recovery only after a live message refers to this copy again."""
        account, cid, ident = _account(account), self._cid(cid), _file_id(ident)
        with self.db.tx_durable() as c:
            self._conversation(c, account, cid)
            row = c.execute("SELECT 1 FROM sync_private_files WHERE account_id=? AND cid=? AND file_id=?", (account, cid, ident)).fetchone()
            if row is None:
                raise PrivateFileError(404, "not_found")
            if not c.execute("SELECT 1 FROM sync_private_file_refs WHERE account_id=? AND file_id=? LIMIT 1", (account, ident)).fetchone():
                raise PrivateFileError(409, "reattachment_required")
            c.execute("DELETE FROM sync_private_recovery_holds WHERE account_id=? AND file_id=?", (account, ident))
            c.execute("UPDATE sync_private_files SET recovery_reason='' WHERE account_id=? AND file_id=?", (account, ident))


def install_conversation_files(app: Any, store: ConversationFiles, caller_dep: Callable[..., Any], *, max_concurrent_uploads: int = 4, max_concurrent_downloads: int = 8) -> None:
    """A session-only door; callers must also publish the file ID through sync."""
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in (max_concurrent_uploads, max_concurrent_downloads)):
        raise ValueError("positive upload and download concurrency limits are required")
    active = 0
    downloads_active = 0
    def done(task: asyncio.Task) -> None:
        nonlocal active
        active -= 1
        if not task.cancelled():
            task.exception()
    def account_of(caller: Any) -> str:
        if getattr(caller, "via", "") in {"", "user"}:
            raise PrivateFileError(403, "session_required")
        return _account(getattr(caller, "account_id", ""))
    def error(exc: PrivateFileError) -> JSONResponse:
        messages = {"storage_full": "Хранилище заполнено. Удалите ненужные файлы и попробуйте снова.",
                    "operation_history_full": "История загрузок заполнена. Обратитесь в поддержку.",
                    "file_too_large": f"Файл слишком большой. Максимальный размер: {store.max_file_bytes} байт.",
                    "upload_busy": "Сейчас много загрузок. Попробуйте позже.",
                    "download_busy": "Сейчас много скачиваний. Попробуйте позже."}
        return JSONResponse({"error": {"code": exc.code, "message": messages.get(exc.code, "Файл недоступен или запрос не выполнен.")}}, status_code=exc.status, headers={"Cache-Control": "no-store"})
    @app.get("/v1/sync/files/recovery")
    async def recovery(request: Request, caller: Any = Depends(caller_dep)) -> Response:
        try:
            raw_limit = request.query_params.get("limit", "50")
            try:
                limit = int(raw_limit)
            except (ValueError, TypeError):
                raise PrivateFileError(400, "invalid_limit") from None
            result = await asyncio.to_thread(store.recovery, account_of(caller), request.query_params.get("after", ""), limit)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except PrivateFileError as exc:
            return error(exc)
    @app.post("/v1/sync/conversations/{cid}/files")
    async def upload(cid: str, request: Request, caller: Any = Depends(caller_dep)) -> Response:
        nonlocal active
        admitted = False
        try:
            account = account_of(caller)
            cid = store._cid(cid)
            encoded_name = request.headers.get("x-file-name-base64", "")
            try:
                if len(encoded_name) > 800:
                    raise ValueError("name is too long")
                name = _name(base64.b64decode(encoded_name, altchars=b"-_", validate=True).decode("utf-8"))
            except (ValueError, UnicodeError, binascii.Error):
                raise PrivateFileError(400, "invalid_name") from None
            mime = _mime(request.headers.get("content-type", ""))
            operation = request.headers.get("idempotency-key", "")
            if not _OPERATION.fullmatch(operation):
                raise PrivateFileError(400, "invalid_idempotency_key")
            if active >= max_concurrent_uploads:
                raise PrivateFileError(503, "upload_busy")
            active += 1
            admitted = True
            raw = bytearray()
            async for chunk in request.stream():
                if len(chunk) > store.max_file_bytes - len(raw):
                    raise PrivateFileError(413, "file_too_large")
                raw.extend(chunk)
            writer = asyncio.create_task(asyncio.to_thread(store.put, account, cid, operation, name, mime, bytes(raw)))
            writer.add_done_callback(done)
            admitted = False
            result = await asyncio.shield(writer)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except PrivateFileError as exc:
            return error(exc)
        finally:
            if admitted:
                active -= 1
    @app.get("/v1/sync/conversations/{cid}/files/{ident}")
    async def download(cid: str, ident: str, caller: Any = Depends(caller_dep)) -> Response:
        nonlocal downloads_active
        try:
            account = account_of(caller)
            if downloads_active >= max_concurrent_downloads:
                raise PrivateFileError(503, "download_busy")
            downloads_active += 1
            released = False
            def release_download() -> None:
                nonlocal downloads_active, released
                if not released:
                    released = True
                    downloads_active -= 1
            def detached_done(task: asyncio.Task) -> None:
                release_download()
                if not task.cancelled():
                    task.exception()
            reader = asyncio.create_task(asyncio.to_thread(store.get, account, cid, ident))
            try:
                meta, body = await asyncio.shield(reader)
                class DownloadResponse(Response):
                    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
                        try:
                            await super().__call__(scope, receive, send)
                        finally:
                            release_download()
                return DownloadResponse(content=body, media_type="application/octet-stream", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Disposition": "attachment; filename=download; filename*=UTF-8''" + quote(meta["name"], safe="")})
            except BaseException:
                reader.add_done_callback(detached_done)
                raise
        except PrivateFileError as exc:
            return error(exc)
    @app.delete("/v1/sync/conversations/{cid}/files/{ident}")
    async def delete(cid: str, ident: str, caller: Any = Depends(caller_dep)) -> Response:
        try:
            await asyncio.to_thread(store.delete, account_of(caller), cid, ident)
            return Response(status_code=204, headers={"Cache-Control": "no-store"})
        except PrivateFileError as exc:
            return error(exc)
    @app.post("/v1/sync/conversations/{cid}/files/{ident}/recovery/resolve")
    async def resolve_recovery(cid: str, ident: str, caller: Any = Depends(caller_dep)) -> Response:
        try:
            await asyncio.to_thread(store.resolve_recovery, account_of(caller), cid, ident)
            return Response(status_code=204, headers={"Cache-Control": "no-store"})
        except PrivateFileError as exc:
            return error(exc)
