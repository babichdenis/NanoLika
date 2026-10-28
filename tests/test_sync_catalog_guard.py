"""A catalogue reconnect must not republish old device-only conversations."""

from types import SimpleNamespace

from nanomuse.server.events import MAIN_THREAD
from nanomuse.sync import engine as m


def test_catalogue_reconnect_keeps_old_unmapped_chats_local():
    e = object.__new__(m.ConversationSync)
    e.state = {
        "account_id": "account",
        "owners": {},
        "cids": {},
        "catalog_mode": True,
        "side_chats": True,
    }
    e._catalog_started_at = 2000000000
    thread = SimpleNamespace(
        id="old-thread",
        created_at="2020-01-01T00:00:00+00:00",
        device="",
        remote_from="",
        main_of="",
    )
    assert not e._eligible(thread)
    e.state["cids"][thread.id] = "shared"
    assert e._eligible(thread)
    e.state["cids"].clear()
    thread.created_at = "2034-01-01T00:00:00+00:00"
    assert e._eligible(thread)
    thread.main_of = "another-account"
    assert not e._eligible(thread)


def test_account_main_change_preserves_separate_transcripts():
    old = SimpleNamespace(
        id=MAIN_THREAD, main_of="", timeline=SimpleNamespace(events=[{"text": "old transcript"}])
    )
    chosen = SimpleNamespace(
        id="chosen-thread",
        main_of="",
        timeline=SimpleNamespace(events=[{"text": "chosen transcript"}]),
    )
    threads = {MAIN_THREAD: old, "chosen-thread": chosen}

    def move(owner, restore, keep=True):
        archived = threads.pop(MAIN_THREAD)
        archived.id = "archived-main"
        archived.main_of = owner
        threads["archived-main"] = archived
        restored = threads.pop(restore)
        restored.id = MAIN_THREAD
        threads[MAIN_THREAD] = restored
        return archived

    e = object.__new__(m.ConversationSync)
    e.state = {
        "account_id": "account",
        "owners": {},
        "cids": {MAIN_THREAD: "old-cid", "chosen-thread": "chosen-cid"},
        "titles": {},
        "catalog_mode": True,
        "side_chats": True,
    }
    e.svc = SimpleNamespace(threads=threads, rehome_main=move)
    e._save = lambda: None
    e._apply_conversation({"cid": "chosen-cid", "kind": "main", "title": "Main chat"})
    assert e.cid_of(MAIN_THREAD) == "chosen-cid"
    assert e.cid_of("archived-main") == "old-cid"
    assert threads[MAIN_THREAD].timeline.events == [{"text": "chosen transcript"}]
    assert threads["archived-main"].timeline.events == [{"text": "old transcript"}]
