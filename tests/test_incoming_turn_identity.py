"""An enqueued turn retains the exact user event and sync identity."""

import pytest

from nanomuse.llm import MockLLM
from nanomuse.server.service import MuseService


def test_same_text_queued_twice_has_two_stable_identities(settings, monkeypatch):
    service = MuseService(settings, llm=MockLLM([]))
    service.timeline("chat")
    monkeypatch.setattr(service, "_ensure_worker", lambda _thread: None)
    a = service.send("chat", "same")
    b = service.send("chat", "same")
    one = service.threads["chat"].inbox.get_nowait()
    two = service.threads["chat"].inbox.get_nowait()
    assert a["id"] != b["id"] and a["mid"] != b["mid"]
    assert (one.user_event_id, one.user_mid) == (a["id"], a["mid"])
    assert (two.user_event_id, two.user_mid) == (b["id"], b["mid"])


@pytest.mark.asyncio
async def test_concurrent_artifacts_keep_the_originating_user_event(settings, monkeypatch):
    import asyncio

    service = MuseService(settings, llm=MockLLM([]))
    for name in ("first", "second"):
        service.timeline(name)
    barrier = asyncio.Barrier(2)

    async def lease(_thread):
        return "", ""

    monkeypatch.setattr(service, "_lease_turn", lease)

    def run_for(name):
        async def run(*_args, **_kwargs):
            await barrier.wait()
            service.ui.emit({"type": "artifact", "thread": name, "path": name + ".txt"})
            return "answer-" + name
        return run

    for name in ("first", "second"):
        monkeypatch.setattr(service.threads[name].agent, "run", run_for(name))
    first = service.send("first", "request")
    second = service.send("second", "request")
    await asyncio.gather(service.threads["first"].worker, service.threads["second"].worker)
    for name, event in (("first", first), ("second", second)):
        rows = service.threads[name].timeline.events
        artifact = next(row for row in rows if row.get("type") == "artifact")
        answer = next(row for row in rows if row.get("type") == "assistant" and row.get("final"))
        assert artifact["reply_to_event"] == answer["reply_to_event"] == event["id"]
        assert answer["mid"]


def test_artifact_fingerprint_refuses_traversal_symlinks_and_large_files(tmp_path):
    import hashlib

    from nanomuse.server.webui import WebUI

    root = tmp_path / "root"
    root.mkdir()
    (root / "file.txt").write_bytes(b"safe")
    (tmp_path / "private.txt").write_bytes(b"private")
    (root / "link.txt").symlink_to(tmp_path / "private.txt")
    (root / "big.txt").write_bytes(b"x" * (5 * 1024 * 1024 + 1))
    assert WebUI._artifact_fingerprint(root, "file.txt") == {"sha256": hashlib.sha256(b"safe").hexdigest(), "size": 4}
    assert WebUI._artifact_fingerprint(root, "../private.txt") == {}
    assert WebUI._artifact_fingerprint(root, "link.txt") == {}
    assert WebUI._artifact_fingerprint(root, "big.txt") == {}


@pytest.mark.asyncio
async def test_updated_artifact_uses_latest_fingerprint(settings):
    import hashlib

    from nanomuse.workspace_context import workspace_context

    service = MuseService(settings, llm=MockLLM([]))
    service.timeline("chat")
    root = settings.agent.workspace
    target = root / "out.txt"
    target.write_bytes(b"first")
    service.ui.enable_artifact_fingerprints("chat")
    with workspace_context("account:fixture", root):
        service.ui._announce_artifact("chat", "out.txt", "write")
        target.write_bytes(b"second")
        service.ui._announce_artifact("chat", "out.txt", "update")
        await service.ui.wait_artifact_fingerprints("chat")
    events = [event for event in service.threads["chat"].timeline.events if event.get("type") == "artifact"]
    assert len(events) == 1 and not events[0]["fingerprint_pending"]
    assert events[0]["sha256"] == hashlib.sha256(b"second").hexdigest() and events[0]["size"] == 6
