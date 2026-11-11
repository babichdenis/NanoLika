"""Bounded, canonical attachment wire format for verified messenger operations."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from pathlib import Path
from typing import Any

MAX_FILES = 4
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 10 * 1024 * 1024
# JSON base64 overhead plus text and metadata; enforced while reading request streams.
MAX_REQUEST_BYTES = (MAX_TOTAL_BYTES * 4 // 3) + 256 * 1024


class AttachmentError(ValueError):
    """Invalid or oversized input; no provider work should be started."""


def normal_name(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 120:
        raise AttachmentError("invalid attachment name")
    # Names are display labels, not paths, and cannot select a filesystem location.
    if value in {".", ".."} or Path(value).name != value or "\\" in value or any(ord(c) < 32 for c in value):
        raise AttachmentError("invalid attachment name")
    return value


def prepare_files(files: Any) -> list[dict[str, Any]]:
    if not isinstance(files, list) or len(files) > MAX_FILES:
        raise AttachmentError("too many attachments")
    total = 0
    out = []
    for item in files:
        name = normal_name(getattr(item, "name", None))
        data = getattr(item, "data", None)
        kind = str(getattr(item, "kind", "file"))
        if not isinstance(data, bytes) or len(data) > MAX_FILE_BYTES:
            raise AttachmentError("invalid attachment size")
        if kind not in {"file", "image", "audio"}:
            raise AttachmentError("invalid attachment kind")
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise AttachmentError("attachments too large")
        out.append({"name": name, "kind": kind, "data": data, "sha256": hashlib.sha256(data).hexdigest()})
    return out


def wire_files(files: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{"name": f["name"], "kind": f["kind"], "sha256": f["sha256"],
             "data": base64.b64encode(f["data"]).decode("ascii")} for f in files]


def parse_files(wire: Any) -> list[dict[str, Any]]:
    if not isinstance(wire, list) or len(wire) > MAX_FILES:
        raise AttachmentError("too many attachments")
    total = 0
    out = []
    for item in wire:
        if not isinstance(item, dict) or set(item) != {"name", "kind", "sha256", "data"}:
            raise AttachmentError("invalid attachment")
        name = normal_name(item["name"])
        kind, digest, encoded = item["kind"], item["sha256"], item["data"]
        if not isinstance(kind, str) or kind not in {"file", "image", "audio"} or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise AttachmentError("invalid attachment")
        if not isinstance(encoded, str) or len(encoded) > ((MAX_FILE_BYTES + 2) // 3) * 4:
            raise AttachmentError("invalid attachment size")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise AttachmentError("invalid attachment encoding") from None
        if len(data) > MAX_FILE_BYTES or hashlib.sha256(data).hexdigest() != digest:
            raise AttachmentError("invalid attachment data")
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise AttachmentError("attachments too large")
        out.append({"name": name, "kind": kind, "data": data, "sha256": digest})
    return out


def save_to_thread(service: Any, thread_id: str, scope: str, operation: str, files: list[dict[str, Any]]) -> list[str]:
    """Store validated files below the verified thread, refusing symlink traversal.

    Each operation gets a private deterministic directory. Only files created by this
    invocation are removed if staging fails; successful files belong to the conversation.
    """
    import os

    from nanomuse.workspace_context import thread_path

    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", operation):
        raise AttachmentError("invalid operation")
    configured = getattr(getattr(getattr(service, "settings", None), "server", None), "max_upload_mb", None)
    if configured is not None and any(len(item["data"]) > int(configured) * 1024 * 1024 for item in files):
        raise AttachmentError("attachment exceeds the account upload limit")
    base = Path(service.data_dir).resolve()
    root = service.workspace_for_thread(thread_id, scope)
    if not root.is_relative_to(base):
        raise AttachmentError("workspace unavailable")
    subdir = ("attachments", "messenger", hashlib.sha256(operation.encode()).hexdigest())
    parts = (*root.relative_to(base).parts, *subdir)
    created: list[str] = []
    descriptor = None
    try:
        descriptor = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in parts:
            try:
                os.mkdir(part, mode=0o700, dir_fd=descriptor)
                os.fsync(descriptor)
            except FileExistsError:
                pass
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = nxt
        result = []
        for index, item in enumerate(files):
            safe = re.sub(r"[^\w.\- ()\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+", "_", item["name"]).strip(" ._")[:100] or "file"
            name = f"{index}-{safe}"
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
            created.append(name)
            with os.fdopen(fd, "wb") as out:
                out.write(item["data"])
                out.flush()
                os.fsync(out.fileno())
            result.append(thread_path(thread_id, "/".join((*subdir, name))))
        os.fsync(descriptor)
        return result
    except (OSError, ValueError, KeyError) as exc:
        if descriptor is not None:
            for name in created:
                try:
                    os.unlink(name, dir_fd=descriptor)
                except OSError:
                    pass
        raise AttachmentError("cannot stage attachments") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def read_from_thread(service: Any, thread_id: str, scope: str, artifacts: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Read only this turn's bounded artifacts; never follow any symlink component."""
    import os
    import stat

    root = service.workspace_for_thread(thread_id, scope)
    data_root = Path(service.data_dir).resolve()
    if not root.is_relative_to(data_root):
        raise AttachmentError("workspace unavailable")
    seen: set[str] = set()
    files = []
    for event in artifacts:
        rel = event.get("path")
        if not isinstance(rel, str) or not rel or rel in seen:
            continue
        seen.add(rel)
        if len(seen) > MAX_FILES:
            raise AttachmentError("too many result files")
        parts = Path(rel).parts
        if Path(rel).is_absolute() or not parts or any(part in {"", ".", ".."} for part in parts):
            raise AttachmentError("invalid result path")
        if parts[0] == "threads":
            if len(parts) < 3 or parts[1] != thread_id:
                raise AttachmentError("result belongs to another conversation")
            parts = parts[2:]
        normal_name(parts[-1])
        descriptors = []
        try:
            current = os.open(data_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptors.append(current)
            for part in (*root.relative_to(data_root).parts, *parts[:-1]):
                current = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
                descriptors.append(current)
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=current)
            descriptors.append(fd)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                raise AttachmentError("invalid result size")
            data = bytearray()
            while len(data) <= MAX_FILE_BYTES:
                chunk = os.read(fd, min(1024 * 1024, MAX_FILE_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) != info.st_size:
                raise AttachmentError("result changed during read")
            digest, size = event.get("sha256"), event.get("size")
            if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or type(size) is not int or size != len(data)
                or hashlib.sha256(data).hexdigest() != digest or event.get("fingerprint_pending")):
                raise AttachmentError("result changed after the response")
            files.append(type("File", (), {"name": parts[-1], "kind": "file", "data": bytes(data)})())
        except (OSError, ValueError, PermissionError) as exc:
            raise AttachmentError("result file unavailable") from exc
        finally:
            for fd in reversed(descriptors):
                os.close(fd)
    return wire_files(prepare_files(files))
