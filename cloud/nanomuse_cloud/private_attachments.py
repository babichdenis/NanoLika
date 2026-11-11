"""Account-authorized private attachments, independent of legacy signed links.

This module is not mounted by default. An integrator must pass the relay's existing
``caller_dep`` to :func:`install_private_attachments` and wire account deletion and
sync consumers before enabling it. File contents are encrypted per account, quotas
are enforced transactionally, and neither file ids nor URLs grant access by themselves.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import sqlite3
import stat
import time
import uuid
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import quote

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Depends, Request
from fastapi.responses import JSONResponse, Response

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ACCOUNT_BYTES = 100 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_ACCOUNT_ROWS = 1000
MAX_TOTAL_ROWS = 100_000
# Files are retained until explicitly deleted. Optional expiry is never enabled implicitly.
DEFAULT_TTL_S: int | None = None
MAX_ACCOUNT_OPERATIONS = 10_000
MAX_TOTAL_OPERATIONS = 100_000
_IDEMPOTENCY = re.compile(r"[A-Za-z0-9_-]{16,128}\Z")
_SERVER_SECRET = re.compile(r"[A-Za-z0-9_-]{43,128}\Z")


def _valid_secret(value: Any) -> bool:
    # Generate with secrets.token_urlsafe(32), not a human password. A length and
    # diversity check catches obvious misconfiguration; it cannot prove entropy.
    return (isinstance(value, str) and bool(_SERVER_SECRET.fullmatch(value))
            and len(set(value)) >= 16)


class PrivateFileError(Exception):
    def __init__(self, status: int, code: str):
        self.status = status
        self.code = code
        super().__init__(code)


def _account(value: Any) -> str:
    try:
        canonical = str(uuid.UUID(str(value or "")))
    except (ValueError, AttributeError, TypeError):
        raise PrivateFileError(401, "sign_in_required") from None
    if canonical != str(value):
        raise PrivateFileError(401, "sign_in_required")
    return canonical


def _file_id(value: str) -> str:
    try:
        canonical = str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        raise PrivateFileError(404, "not_found") from None
    if canonical != value:
        raise PrivateFileError(404, "not_found")
    return canonical


def _name(value: str) -> str:
    if (not isinstance(value, str) or not value or len(value) > 160 or len(value.encode("utf-8")) > 512
        or value in {".", ".."} or "/" in value or "\\" in value or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise PrivateFileError(400, "invalid_filename")
    return value


def _mime(value: str) -> str:
    value = str(value or "application/octet-stream").split(";", 1)[0].strip().lower()
    if len(value) > 120 or not re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", value):
        raise PrivateFileError(400, "invalid_content_type")
    return value


class PrivateAttachments:
    """An atomic encrypted blob store; one SQLite transaction covers quota and file."""

    def __init__(self, directory: Path, secret: str, *, previous_secrets: tuple[str, ...] = (),
                 clock: Callable[[], float] = time.time, ttl_seconds: int | None = DEFAULT_TTL_S, max_file_bytes: int = MAX_FILE_BYTES,
                 max_account_bytes: int = MAX_ACCOUNT_BYTES, max_total_bytes: int = MAX_TOTAL_BYTES,
                 max_account_rows: int = MAX_ACCOUNT_ROWS, max_total_rows: int = MAX_TOTAL_ROWS,
                 max_account_operations: int = MAX_ACCOUNT_OPERATIONS,
                 max_total_operations: int = MAX_TOTAL_OPERATIONS):
        if not _valid_secret(secret):
            raise ValueError("a random server secret of at least 32 bytes is required")
        if (ttl_seconds is not None and ttl_seconds <= 0) or min(max_file_bytes, max_account_bytes, max_total_bytes, max_account_rows, max_total_rows, max_account_operations, max_total_operations) <= 0:
            raise ValueError("positive limits are required")
        self.root = Path(directory)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValueError("unsafe storage directory")
        os.chmod(self.root, 0o700)
        self.path = self.root / "private-attachments.sqlite3"
        self.clock, self.ttl_seconds = clock, int(ttl_seconds) if ttl_seconds is not None else None
        self.max_file_bytes, self.max_account_bytes, self.max_total_bytes = max_file_bytes, max_account_bytes, max_total_bytes
        self.max_account_rows, self.max_total_rows = max_account_rows, max_total_rows
        self.max_account_operations, self.max_total_operations = max_account_operations, max_total_operations
        if any(not _valid_secret(value) for value in previous_secrets):
            raise ValueError("previous secrets must also be random server secrets")
        masters = [hmac.new(value.encode(), b"nanolika:private-attachments:v1", hashlib.sha256).digest()
                   for value in (secret, *previous_secrets)]
        self._masters = {hashlib.sha256(value).hexdigest()[:16]: value for value in masters}
        self._key_id = hashlib.sha256(masters[0]).hexdigest()[:16]
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        except FileExistsError:
            info = self.path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ValueError("unsafe storage file") from None
        with closing(self._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS private_attachments (
                file_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, operation TEXT NOT NULL,
                request_digest TEXT NOT NULL, name TEXT NOT NULL, mime TEXT NOT NULL,
                size INTEGER NOT NULL, created INTEGER NOT NULL, expires INTEGER,
                key_id TEXT NOT NULL, nonce BLOB, ciphertext BLOB, deleted INTEGER NOT NULL DEFAULT 0,
                UNIQUE(account_id, operation))""")
            # Tombstones contain no file content or name and outlive all retries.
            db.execute("""CREATE TABLE IF NOT EXISTS private_attachment_operations (
                account_id TEXT NOT NULL, operation TEXT NOT NULL,
                PRIMARY KEY(account_id, operation))""")
            # Fence requests authenticated before the account deletion began.
            db.execute("""CREATE TABLE IF NOT EXISTS private_attachment_deleted_accounts (
                account_id TEXT PRIMARY KEY)""")
            db.execute("INSERT OR IGNORE INTO private_attachment_operations SELECT account_id,operation FROM private_attachments WHERE deleted=1")
            db.execute("DELETE FROM private_attachments WHERE deleted=1")
            db.execute("CREATE INDEX IF NOT EXISTS private_attachments_account ON private_attachments(account_id, expires)")
            db.execute("CREATE INDEX IF NOT EXISTS private_attachments_expiry ON private_attachments(expires)")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA secure_delete=ON")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def _key(self, account: str, key_id: str) -> bytes:
        master = self._masters.get(key_id)
        if master is None:
            raise PrivateFileError(503, "storage_key_unavailable")
        return hmac.new(master, account.encode(), hashlib.sha256).digest()

    @staticmethod
    def _aad(account: str, ident: str, name: str, mime: str, size: int, expires: int | None) -> bytes:
        return json.dumps([account, ident, name, mime, size, expires], ensure_ascii=False, separators=(",", ":")).encode()

    @staticmethod
    def _metadata(row: sqlite3.Row) -> dict[str, Any]:
        return {"file_id": row["file_id"], "name": row["name"], "mime": row["mime"],
                "size": row["size"], "created_at": row["created"], "expires_at": row["expires"]}

    def put(self, account: str, operation: str, name: str, mime: str, data: bytes) -> dict[str, Any]:
        account, name, mime = _account(account), _name(name), _mime(mime)
        if not isinstance(operation, str) or not _IDEMPOTENCY.fullmatch(operation):
            raise PrivateFileError(400, "invalid_idempotency_key")
        if not isinstance(data, bytes) or len(data) > self.max_file_bytes:
            raise PrivateFileError(413, "file_too_large")
        now = int(self.clock())
        digest = hashlib.sha256(json.dumps([name, mime, hashlib.sha256(data).hexdigest()], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        operation_hash = hashlib.sha256(operation.encode()).hexdigest()
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM private_attachment_deleted_accounts WHERE account_id=?", (account,)).fetchone():
                raise PrivateFileError(403, "account_deleted")
            # Preserve expiration in the same durable operation ledger as deletion.
            self._expire(db, now)
            if db.execute("SELECT 1 FROM private_attachment_operations WHERE account_id=? AND operation=?", (account, operation_hash)).fetchone():
                raise PrivateFileError(409, "operation_conflict")
            prior = db.execute("SELECT * FROM private_attachments WHERE account_id=? AND operation=?", (account, operation_hash)).fetchone()
            if prior:
                if prior["request_digest"] != digest or prior["deleted"]:
                    raise PrivateFileError(409, "operation_conflict")
                return self._metadata(prior)
            used, count = db.execute("SELECT COALESCE(SUM(CASE WHEN deleted=0 THEN size ELSE 0 END),0), COUNT(*) FROM private_attachments WHERE account_id=?", (account,)).fetchone()
            total, total_count = db.execute("SELECT COALESCE(SUM(CASE WHEN deleted=0 THEN size ELSE 0 END),0), COUNT(*) FROM private_attachments").fetchone()
            if used + len(data) > self.max_account_bytes or total + len(data) > self.max_total_bytes or count >= self.max_account_rows or total_count >= self.max_total_rows:
                raise PrivateFileError(507, "storage_full")
            # Reserve a permanent operation id while the file is live, so deletion
            # remains possible even when the independent operation quota is full.
            account_ids = db.execute("SELECT COUNT(*) FROM private_attachment_operations WHERE account_id=?", (account,)).fetchone()[0]
            all_ids = db.execute("SELECT COUNT(*) FROM private_attachment_operations").fetchone()[0]
            if count + account_ids >= self.max_account_operations or total_count + all_ids >= self.max_total_operations:
                raise PrivateFileError(507, "operation_history_full")
            ident = str(uuid.uuid4())
            expires = now + self.ttl_seconds if self.ttl_seconds is not None else None
            nonce = os.urandom(12)
            encrypted = AESGCM(self._key(account, self._key_id)).encrypt(nonce, data, self._aad(account, ident, name, mime, len(data), expires))
            db.execute("INSERT INTO private_attachments(file_id,account_id,operation,request_digest,name,mime,size,created,expires,key_id,nonce,ciphertext) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (ident, account, operation_hash, digest, name, mime, len(data), now, expires, self._key_id, nonce, encrypted))
            row = db.execute("SELECT * FROM private_attachments WHERE file_id=?", (ident,)).fetchone()
            return self._metadata(row)

    def get(self, account: str, ident: str) -> tuple[dict[str, Any], bytes]:
        account, ident = _account(account), _file_id(ident)
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM private_attachments WHERE account_id=? AND file_id=? AND deleted=0 AND (expires IS NULL OR expires>?)", (account, ident, int(self.clock()))).fetchone()
        if row is None:
            raise PrivateFileError(404, "not_found")
        try:
            content = AESGCM(self._key(account, row["key_id"])).decrypt(row["nonce"], row["ciphertext"], self._aad(account, ident, row["name"], row["mime"], row["size"], row["expires"]))
        except (InvalidTag, TypeError, ValueError):
            raise PrivateFileError(503, "storage_unavailable") from None
        if len(content) != row["size"]:
            raise PrivateFileError(503, "storage_unavailable")
        return self._metadata(row), content

    @staticmethod
    def _expire(db: sqlite3.Connection, now: int) -> int:
        db.execute("INSERT OR IGNORE INTO private_attachment_operations SELECT account_id,operation FROM private_attachments WHERE expires IS NOT NULL AND expires<=?", (now,))
        return db.execute("DELETE FROM private_attachments WHERE expires IS NOT NULL AND expires<=?", (now,)).rowcount

    def delete(self, account: str, ident: str) -> None:
        account, ident = _account(account), _file_id(ident)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT operation FROM private_attachments WHERE account_id=? AND file_id=? AND deleted=0 AND (expires IS NULL OR expires>?)", (account, ident, int(self.clock()))).fetchone()
            if row is None:
                raise PrivateFileError(404, "not_found")
            db.execute("INSERT OR IGNORE INTO private_attachment_operations VALUES (?,?)", (account, row["operation"]))
            db.execute("DELETE FROM private_attachments WHERE account_id=? AND file_id=?", (account, ident))

    def purge_account(self, account: str) -> None:
        """Permanently fence new uploads and erase this account's private objects.

        The relay must call this after disabling login in its account lifecycle.
        The marker is checked within the upload transaction, including for requests
        whose authentication completed before the account was disabled.
        """
        account = _account(account)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO private_attachment_deleted_accounts VALUES (?)", (account,))
            db.execute("DELETE FROM private_attachments WHERE account_id=?", (account,))
            db.execute("DELETE FROM private_attachment_operations WHERE account_id=?", (account,))

    def cleanup_expired(self) -> int:
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            return self._expire(db, int(self.clock()))


def install_private_attachments(app: Any, store: PrivateAttachments, caller_dep: Callable[..., Any],
                                *, max_concurrent_uploads: int = 4) -> None:
    """Install separate bearer-authenticated endpoints without changing legacy routes."""
    if isinstance(max_concurrent_uploads, bool) or not isinstance(max_concurrent_uploads, int) or max_concurrent_uploads <= 0:
        raise ValueError("a positive upload concurrency limit is required")
    # Per process, on its event loop. In a multi-process deployment the overall
    # bound is this value times the number of workers; configure accordingly.
    active_uploads = 0

    def release_writer(task: asyncio.Task) -> None:
        nonlocal active_uploads
        active_uploads -= 1
        # A disconnected client no longer awaits the task; consume its exception.
        if not task.cancelled():
            task.exception()
    def error_response(exc: PrivateFileError) -> JSONResponse:
        messages = {
            "storage_full": "Хранилище заполнено. Удалите ненужные файлы и попробуйте снова.",
            "operation_history_full": "История загрузок заполнена. Обратитесь в поддержку.",
            "account_deleted": "Аккаунт удалён; загрузка файлов недоступна.",
            "upload_busy": "Сейчас много загрузок. Попробуйте ещё раз чуть позже.",
            "file_too_large": f"Файл слишком большой. Максимальный размер: {store.max_file_bytes} байт.",
            "operation_conflict": "Этот запрос уже использован для другого или удалённого файла.",
            "not_found": "Файл не найден или недоступен.",
            "session_required": "Войдите в аккаунт, чтобы открыть файл.",
            "sign_in_required": "Войдите в аккаунт, чтобы открыть файл.",
            "storage_key_unavailable": "Файл временно недоступен. Обратитесь в поддержку.",
            "storage_unavailable": "Файл временно недоступен. Обратитесь в поддержку.",
            "invalid_filename": "Не удалось принять имя файла.",
            "invalid_content_type": "Не удалось определить тип файла.",
            "invalid_idempotency_key": "Не удалось подтвердить запрос на загрузку. Попробуйте ещё раз.",
        }
        return JSONResponse({"error": {"code": exc.code, "message": messages.get(exc.code, "Не удалось обработать файл.")}},
                            status_code=exc.status, headers={"Cache-Control": "no-store"})
    def account_of(caller: Any) -> str:
        if getattr(caller, "via", "") in {"", "user"}:
            raise PrivateFileError(403, "session_required")
        return _account(getattr(caller, "account_id", ""))

    @app.post("/v1/private-attachments")
    async def upload(request: Request, caller: Any = Depends(caller_dep)) -> Response:
        nonlocal active_uploads
        admitted = False
        try:
            account = account_of(caller)
            name = _name(request.query_params.get("name", ""))
            mime = _mime(request.headers.get("content-type", ""))
            operation = request.headers.get("idempotency-key", "")
            if not _IDEMPOTENCY.fullmatch(operation):
                raise PrivateFileError(400, "invalid_idempotency_key")
            # No await between the check and increment: admission is atomic among
            # coroutines on this process's event loop, before reading any body.
            if active_uploads >= max_concurrent_uploads:
                raise PrivateFileError(503, "upload_busy")
            active_uploads += 1
            admitted = True
            raw = bytearray()
            async for chunk in request.stream():
                if len(chunk) > store.max_file_bytes - len(raw):
                    raise PrivateFileError(413, "file_too_large")
                raw.extend(chunk)
            # A thread cannot be cancelled once SQLite is writing. Transfer the slot
            # to the task and release it only when the underlying work actually ends.
            writer = asyncio.create_task(asyncio.to_thread(store.put, account, operation, name, mime, bytes(raw)))
            writer.add_done_callback(release_writer)
            admitted = False
            metadata = await asyncio.shield(writer)
            return JSONResponse(metadata, headers={"Cache-Control": "no-store"})
        except PrivateFileError as exc:
            return error_response(exc)
        finally:
            if admitted:
                active_uploads -= 1

    @app.get("/v1/private-attachments/{ident}")
    async def download(ident: str, caller: Any = Depends(caller_dep)) -> Response:
        try:
            metadata, body = await asyncio.to_thread(store.get, account_of(caller), ident)
            return Response(content=body, media_type="application/octet-stream", headers={
                "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                "Content-Disposition": "attachment; filename=download; filename*=UTF-8''" + quote(metadata["name"], safe=""),
            })
        except PrivateFileError as exc:
            return error_response(exc)

    @app.delete("/v1/private-attachments/{ident}")
    async def delete(ident: str, caller: Any = Depends(caller_dep)) -> Response:
        try:
            await asyncio.to_thread(store.delete, account_of(caller), ident)
            return Response(status_code=204, headers={"Cache-Control": "no-store"})
        except PrivateFileError as exc:
            return error_response(exc)
