"""Studio sessions persist per runtime without repeating paid operations."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nanomuse.avatar.studio import AvatarStudio, Session
from nanomuse.timeutil import account_runtime


def studio_for(svc, account="account-a"):
    with account_runtime(account):
        return AvatarStudio(svc)


def service(path):
    path.mkdir(parents=True, exist_ok=True)
    bus = SimpleNamespace(publish=lambda _: None)
    ui = SimpleNamespace(bus=bus, patch=lambda *a, **k: None)
    workspace = path / "workspace"
    workspace.mkdir(exist_ok=True)
    return SimpleNamespace(data_dir=path, ui=ui, threads={}, workspace=lambda: workspace)


@pytest.mark.asyncio
async def test_two_account_sessions_recover_separately_without_repeat(tmp_path, monkeypatch):
    monkeypatch.delenv("NANOMUSE_SHARED_CLOUD_BOT", raising=False)
    a = studio_for(service(tmp_path / "a"))
    b = studio_for(service(tmp_path / "b"), "account-b")
    a.current = Session(id="a123456789", thread="", event_id="", description="Лиса", stage="choose", candidates=["avatar/sessions/a123456789/c0.webp", None, None, None])
    b.current = Session(id="b123456789", thread="", event_id="", description="Кот", stage="choose", candidates=["avatar/sessions/b123456789/c0.webp", None, None, None])
    for studio in (a, b):
        path = studio.svc.workspace() / studio.current.candidates[0]
        path.parent.mkdir(parents=True)
        path.write_bytes(b"fixture")
    a._patch(a.current)
    b._patch(b.current)
    await a.close()
    await b.close()
    a2 = studio_for(service(tmp_path / "a"))
    b2 = studio_for(service(tmp_path / "b"), "account-b")
    try:
        assert a2.current.id == "a123456789" and a2.current.description == "Лиса"
        assert b2.current.id == "b123456789" and b2.current.description == "Кот"
        assert a2.current.stage == b2.current.stage == "choose"
        assert a2.current.tasks == b2.current.tasks == []
    finally:
        await a2.close()
        await b2.close()


@pytest.mark.asyncio
async def test_interrupted_paid_run_is_not_restarted_on_recovery(tmp_path, monkeypatch):
    monkeypatch.delenv("NANOMUSE_SHARED_CLOUD_BOT", raising=False)
    svc = service(tmp_path / "a")
    old = studio_for(svc)
    old.current = Session(id="a123456789", thread="", event_id="", description="Лиса", stage="drawing")
    old._patch(old.current)
    await old.close()
    restored = studio_for(svc)
    try:
        assert restored.current.stage == "failed"
        assert restored.current.tasks == []
        assert restored.current.message
    finally:
        await restored.close()


@pytest.mark.asyncio
async def test_shared_bot_does_not_load_stored_installation_session(tmp_path, monkeypatch):
    monkeypatch.delenv("NANOMUSE_SHARED_CLOUD_BOT", raising=False)
    svc = service(tmp_path / "a")
    old = studio_for(svc)
    old.current = Session(id="a123456789", thread="", event_id="", description="private", stage="choose")
    old._patch(old.current)
    await old.close()
    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    shared = studio_for(svc)
    try:
        assert shared.current is None
    finally:
        await shared.close()


@pytest.mark.asyncio
async def test_missing_candidate_and_traversal_are_not_restored(tmp_path, monkeypatch):
    import json

    monkeypatch.delenv("NANOMUSE_SHARED_CLOUD_BOT", raising=False)
    svc = service(tmp_path / "a")
    old = studio_for(svc)
    old.current = Session(id="a123456789", thread="", event_id="", description="Лиса", stage="choose", candidates=["avatar/sessions/a123456789/c0.webp", None, None, None])
    old._patch(old.current)
    await old.close()
    restored = studio_for(svc)
    assert restored.current.stage == "failed"
    assert restored.current.candidates == [None] * 4
    await restored.close()
    path = svc.data_dir / "avatar-studio.json"
    raw = json.loads(path.read_text())
    raw["candidates"][0] = "../../private.webp"
    path.write_text(json.dumps(raw))
    unsafe = studio_for(svc)
    try:
        assert unsafe.current is None
    finally:
        await unsafe.close()


@pytest.mark.asyncio
async def test_ownerless_and_different_account_never_restore_existing_session(tmp_path, monkeypatch):
    monkeypatch.delenv("NANOMUSE_SHARED_CLOUD_BOT", raising=False)
    svc = service(tmp_path / "a")
    original = studio_for(svc)
    original.current = Session(id="a123456789", thread="", event_id="", description="private", stage="estimate")
    original._patch(original.current)
    await original.close()
    wrong = studio_for(svc, "different-account")
    ownerless = AvatarStudio(svc)
    try:
        assert wrong.current is None
        assert ownerless.current is None
    finally:
        await wrong.close()
        await ownerless.close()


@pytest.mark.asyncio
async def test_real_account_runtime_eviction_restores_only_its_own_choice(tmp_path, monkeypatch):
    from nanomuse.config import Settings
    from nanomuse.server.accounts import AccountRuntime

    monkeypatch.delenv("NANOMUSE_SHARED_CLOUD_BOT", raising=False)
    runtime = AccountRuntime(Settings(), tmp_path / "accounts", limit=1)

    async def build(account, key=""):
        svc = service(runtime.data_dir(account))
        svc.avatar = AvatarStudio(svc)
        svc.threads = {}
        svc.stop = svc.avatar.close
        return svc

    runtime._build = build
    first = await runtime.service("account-a")
    first.avatar.current = Session(id="a123456789", thread="", event_id="", description="Лиса", stage="choose", candidates=["avatar/sessions/a123456789/c0.webp", None, None, None])
    candidate = first.workspace() / first.avatar.current.candidates[0]
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"fixture")
    first.avatar._patch(first.avatar.current)
    second = await runtime.service("account-b")
    assert second.avatar.current is None
    again = await runtime.service("account-a")
    try:
        assert again is not first
        assert again.avatar.current.description == "Лиса"
        assert again.avatar.current.stage == "choose"
        assert again.avatar.current.tasks == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_two_accounts_use_only_their_own_keys_files_and_events(tmp_path, monkeypatch):
    import asyncio
    import base64
    import io

    import httpx
    from PIL import Image

    from nanomuse.config import Settings

    monkeypatch.delenv("NANOMUSE_SHARED_CLOUD_BOT", raising=False)
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), (123, 45, 67)).save(buf, "PNG")
    data = base64.b64encode(buf.getvalue()).decode()
    sent = []
    events = {"a": [], "b": []}
    studios = []
    for name in ("a", "b"):
        svc = service(tmp_path / name)
        svc.ui.bus.publish = lambda value, name=name: events[name].append(value)
        svc.profile = SimpleNamespace(avatar="dragon")
        svc.settings = Settings()
        svc.hub = SimpleNamespace(signed_in=True, cloud=SimpleNamespace(base_url="https://cloud.example"), models=[])
        svc.app = SimpleNamespace(vault=SimpleNamespace(
            has_placeholders=lambda _: False,
            get=lambda _key, name=name: "key-" + name,
        ))
        svc.update_profile = lambda patch, svc=svc: setattr(svc.profile, "avatar", patch["avatar"])
        studio = studio_for(svc, "account-" + name)
        await studio._http.aclose()
        def handler(request, name=name):
            sent.append((name, request.headers.get("authorization", "")))
            return httpx.Response(200, json={"data": [{"b64_json": data}]})
        studio._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        # Keep this test on images; endpoint selection and credential lookup are real.
        studio._cloud_model = lambda kind, default: "image" if kind == "image" else ""
        assert studio.endpoint().api_key == "key-" + name
        studio.current = Session(id=name + "123456789", thread="", event_id="", description="Лиса " + name)
        studios.append(studio)
    try:
        await asyncio.gather(*(studio.start(studio.current.id) for studio in studios))
        await asyncio.gather(*(task for studio in studios for task in studio.current.tasks))
        assert all(studio.current.stage == "choose" for studio in studios)
        await asyncio.gather(*(studio.choose(studio.current.id, 0) for studio in studios))
        await asyncio.gather(*(task for studio in studios for task in studio.current.tasks))
        assert all(studio.current.stage == "done" for studio in studios)
        assert set(sent) == {("a", "Bearer key-a"), ("b", "Bearer key-b")}
        for name, studio in zip(("a", "b"), studios, strict=True):
            assert studio.svc.profile.avatar == studio.current.face
            assert (studio.svc.workspace() / "avatar" / studio.current.face / "idle.webp").is_file()
            assert all(item["current"]["description"] == "Лиса " + name for item in events[name])
    finally:
        await asyncio.gather(*(studio.close() for studio in studios))
