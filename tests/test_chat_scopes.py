"""Cloud identity, archive and device-only history are separate properties."""
from types import SimpleNamespace
import json
from unittest.mock import AsyncMock

import pytest

from nanomuse.server.service import ConversationSync
from nanomuse.channels.manager import ChannelManager
from nanomuse.cloud import CloudError


def thread(tid):
    return SimpleNamespace(id=tid, meta=lambda: {"id": tid, "title": tid})


def test_scope_rows_preserve_archived_cloud_identity_and_local_history():
    rows = [thread("main"), thread("old"), thread("local"), thread("deleted")]
    engine = object.__new__(ConversationSync)
    engine.state = {
        "catalog_mode": True, "cids": {"main": "c1", "old": "c2", "deleted": "gone"},
        "conversation_marks": {}, "local_archives": {},
    }
    engine.catalog = SimpleNamespace(supported=True, rows={"c1": {"archived": False}, "c2": {"archived": True}})
    engine.svc = SimpleNamespace(visible_threads=lambda: rows)
    result = {r["id"]: r for r in engine.list_rows()}
    assert not result["main"]["archived"] and not result["main"]["local_only"]
    assert result["old"]["archived"] and result["old"]["sync_cid"] == "c2"
    assert result["local"]["local_only"] and not result["local"]["archived"]
    assert result["deleted"]["archived"]
    engine.state["local_archives"]["local"] = True
    assert next(r for r in engine.list_rows() if r["id"] == "local")["archived"]


def test_restart_retains_catalogue_guard_and_archive_marks(tmp_path):
    engine = object.__new__(ConversationSync)
    engine.state = {"cids": {}, "owners": {}, "account_id": "account"}
    engine.path = tmp_path / "sync.json"
    engine.path.write_text(json.dumps({"catalog_mode": True, "conversation_marks": {"c2": {"archived": True}}, "local_archives": {"old-local": True}}))
    engine._load()
    assert engine.state["catalog_mode"] is True
    assert engine.state["conversation_marks"]["c2"]["archived"] is True
    assert engine.state["local_archives"]["old-local"] is True
    engine._catalog_started_at = 2000000000
    old = SimpleNamespace(id="old-local", device="", remote_from="", main_of="", created_at="2020-01-01T00:00:00+00:00")
    assert not engine._eligible(old)


@pytest.mark.asyncio
async def test_max_archived_selection_opens_picker_without_starting_model():
    manager = object.__new__(ChannelManager)
    target = SimpleNamespace(id="old")
    manager.svc = SimpleNamespace(threads={"old": target}, sync=SimpleNamespace(
        active=True, cid_of=lambda _: "old-cid", _eligible=lambda _: True,
        hydrate_thread=AsyncMock(side_effect=CloudError(409, "conversation_archived", "archived")),
    ))
    manager.picker = SimpleNamespace(show=AsyncMock())
    msg = SimpleNamespace(channel="max")
    assert await manager._hydrate_shared_thread("old", msg) is False
    manager.picker.show.assert_awaited_once_with(msg)
