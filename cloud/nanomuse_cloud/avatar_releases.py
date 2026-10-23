"""Operator-controlled, immutable default-avatar media. Never changes an account profile."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import struct
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

MOODS = ("idle", "working", "waiting", "happy", "error")
FILES = tuple(f"{m}.webp" for m in MOODS) + tuple(f"{m}.mp4" for m in MOODS if m != "error")
MAX_BUNDLE = 40 * 1024 * 1024
_LOCK = threading.RLock()


def _bad(message: str) -> None:
    raise ValueError(message)


def webp_size(data: bytes) -> tuple[int, int]:
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        _bad("Expected a WebP image")
    kind = data[12:16]
    if kind == b"VP8X":
        return int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1
    if kind == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
        return int.from_bytes(data[26:28], "little") & 0x3FFF, int.from_bytes(data[28:30], "little") & 0x3FFF
    if kind == b"VP8L" and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    _bad("Unsupported WebP image")
    raise AssertionError


def boxes(data: bytes):
    pos = 0
    while pos < len(data):
        if len(data) - pos < 8:
            _bad("Truncated MP4 box")
        size, kind = struct.unpack_from(">I4s", data, pos)
        header = 8
        if size == 1:
            if len(data) - pos < 16:
                _bad("Truncated MP4 box")
            size, header = struct.unpack_from(">Q", data, pos + 8)[0], 16
        if size == 0:
            size = len(data) - pos
        if size < header or pos + size > len(data):
            _bad("Invalid MP4 box size")
        yield kind, data[pos + header : pos + size]
        pos += size


def validate_mp4(data: bytes) -> None:
    top = dict(boxes(data))
    if not all(k in top for k in (b"ftyp", b"moov", b"mdat")):
        _bad("Expected a complete MP4")
    movie = list(boxes(top[b"moov"]))
    tracks = [dict(boxes(value)) for kind, value in movie if kind == b"trak"]
    if len(tracks) != 1:
        _bad("Expected one silent video track")
    track = tracks[0]
    mdia = dict(boxes(track.get(b"mdia", b"")))
    handler = mdia.get(b"hdlr", b"")
    if len(handler) < 12 or handler[8:12] != b"vide":
        _bad("Audio and non-video tracks are not allowed")
    tkhd = track.get(b"tkhd", b"")
    if len(tkhd) < 8 or struct.unpack(">II", tkhd[-8:]) != (512 << 16, 512 << 16):
        _bad("Clips must be 512 by 512")
    stbl = dict(boxes(dict(boxes(mdia.get(b"minf", b""))).get(b"stbl", b"")))
    stsd = stbl.get(b"stsd", b"")
    if len(stsd) < 16 or stsd[12:16] not in (b"avc1", b"avc3"):
        _bad("Clips must use H.264")
    mdhd = mdia.get(b"mdhd", b"")
    if len(mdhd) < 24:
        _bad("Missing clip duration")
    if mdhd[0] == 1:
        if len(mdhd) < 32:
            _bad("Invalid clip duration")
        timescale, duration = struct.unpack_from(">IQ", mdhd, 20)
    else:
        timescale, duration = struct.unpack_from(">II", mdhd, 12)
    if not timescale or not 10 <= duration / timescale <= 15:
        _bad("Clips must be between ten and fifteen seconds")


class AvatarReleases:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _atomic(self, path: Path, data: Any) -> None:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".writing-")
        try:
            with os.fdopen(fd, "w") as out:
                json.dump(data, out, ensure_ascii=False)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, path)
        finally:
            Path(tmp).unlink(missing_ok=True)

    def upload(self, payload: bytes) -> dict:
        if len(payload) > MAX_BUNDLE:
            _bad("Bundle is too large")
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                entries = archive.infolist()
                expected = set(FILES) | {"manifest.json"}
                if len(entries) != len(expected) or {e.filename for e in entries} != expected:
                    _bad("Bundle must contain the nine media files and manifest.json at its root")
                if sum(e.file_size for e in entries) > MAX_BUNDLE:
                    _bad("Unpacked bundle is too large")
                if any(e.flag_bits & 1 or e.file_size > 8 * 1024 * 1024 for e in entries):
                    _bad("Encrypted or oversized entries are not allowed")
                raw = {name: archive.read(name) for name in expected}
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as e:
            raise ValueError("Invalid ZIP bundle") from e
        try:
            meta = json.loads(raw.pop("manifest.json"))
            label = str(meta["label"]).strip()
            if not 1 <= len(label) <= 100:
                _bad("A short label is required")
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ValueError("Invalid manifest label") from e
        for name, data in raw.items():
            if name.endswith(".webp"):
                if webp_size(data) != (768, 768):
                    _bad("Stills must be 768 by 768")
            else:
                validate_mp4(data)
        digest = hashlib.sha256()
        for name in sorted(raw):
            digest.update(name.encode() + b"\0" + raw[name])
        release = digest.hexdigest()[:24]
        target = self.root / release
        with _LOCK:
            if target.exists():
                return self.release(release)
            temporary = Path(tempfile.mkdtemp(dir=self.root, prefix=".upload-"))
            try:
                for name, data in raw.items():
                    (temporary / name).write_bytes(data)
                manifest = {
                    "id": release,
                    "label": label,
                    "created_at": int(time.time()),
                    "files": {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)} for name, data in raw.items()},
                }
                self._atomic(temporary / "manifest.json", manifest)
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        return manifest

    def release(self, release: str) -> dict:
        if not re.fullmatch(r"[a-f0-9]{24}", release):
            _bad("Invalid release ID")
        try:
            return json.loads((self.root / release / "manifest.json").read_text())
        except FileNotFoundError as e:
            raise ValueError("Release does not exist") from e

    def state(self) -> dict:
        try:
            return json.loads((self.root / "active.json").read_text())
        except FileNotFoundError:
            return {"revision": 0, "active": None, "previous": None}

    def publish(self, release: str | None, expected_revision: int) -> dict:
        with _LOCK:
            state = self.state()
            if state["revision"] != expected_revision:
                raise HTTPException(409, "Refresh the list before publishing")
            if release is not None:
                self.release(release)
            new = {"revision": state["revision"] + 1, "active": release, "previous": state["active"], "published_at": int(time.time())}
            self._atomic(self.root / "active.json", new)
            return new

    def public(self) -> dict:
        state = self.state()
        if not state["active"]:
            return {**state, "stills": {}, "clips": {}}
        pack = self.release(state["active"])
        base = "/v1/brand/avatar/assets/" + pack["id"] + "/"
        return {
            **state,
            "label": pack["label"],
            "stills": {m: base + m + ".webp" for m in MOODS},
            "clips": {m: base + m + ".mp4" for m in MOODS if m != "error"},
        }


def install_avatar_releases(app, settings, admin_dep) -> None:
    root = Path(os.environ.get("AVATAR_RELEASES_DIR") or Path(settings.database).parent / "avatar-releases")
    store = AvatarReleases(root)
    app.state.avatar_releases = store

    @app.get("/v1/brand/avatar")
    async def current_avatar():
        return JSONResponse(store.public(), headers={"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*"})

    @app.get("/v1/brand/avatar/assets/{release}/{filename}")
    async def avatar_asset(release: str, filename: str):
        try:
            manifest = store.release(release)
        except ValueError as e:
            raise HTTPException(404, "Not found") from e
        if filename not in FILES:
            raise HTTPException(404, "Not found")
        return FileResponse(
            store.root / release / filename,
            media_type="image/webp" if filename.endswith(".webp") else "video/mp4",
            headers={
                "Cache-Control": "public, max-age=31536000, immutable",
                "ETag": '"' + manifest["files"][filename]["sha256"] + '"',
                "X-Content-Type-Options": "nosniff",
                "Access-Control-Allow-Origin": "*",
            },
        )

    @app.get("/v1/admin/avatar-releases", dependencies=[Depends(admin_dep)])
    async def list_releases():
        releases = [store.release(p.name) for p in store.root.iterdir() if re.fullmatch(r"[a-f0-9]{24}", p.name) and p.is_dir()]
        return {**store.state(), "releases": sorted(releases, key=lambda p: p["created_at"], reverse=True)}

    @app.post("/v1/admin/avatar-releases/upload", dependencies=[Depends(admin_dep)])
    async def upload_release(request: Request):
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_BUNDLE:
                raise HTTPException(413, "Bundle is too large")
        try:
            return store.upload(bytes(data))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/v1/admin/avatar-releases/publish", dependencies=[Depends(admin_dep)])
    async def publish_release(request: Request):
        try:
            body = await request.json()
            revision = body["expected_revision"]
            release = body["release"]
            if type(revision) is not int or (release is not None and not isinstance(release, str)):
                _bad("Invalid publication request")
            return store.publish(release, revision)
        except (ValueError, KeyError, TypeError) as e:
            raise HTTPException(400, str(e)) from e
