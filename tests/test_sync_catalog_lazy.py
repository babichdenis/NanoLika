"""Guards for the lazy catalogue: the cursor only moves forward, a long history is not cut
at fifty pages, and a relay without the catalogue does not leave the device in catalogue
mode forever."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from nanomuse.cloud import CloudError
from nanomuse.server import create_app  # noqa: F401  (the server half is imported first, as in test_sync)
from nanomuse.sync import catalog as catalog_mod
from nanomuse.sync import engine as engine_mod
from nanomuse.sync.catalog import LazyCatalog

ConversationSync = engine_mod.ConversationSync


class FakeClient:
    def __init__(self, *, cursor: int = 10, pages: int = 1, fail: CloudError | None = None) -> None:
        self.cursor = cursor
        self.pages = pages
        self.fail = fail
        self.message_calls: list[int] = []
        self.reads: list[int] = []

    async def catalog(self, device: str, client: str) -> dict[str, Any]:
        if self.fail is not None:
            raise self.fail
        return {
            "account_id": "acc",
            "conversations": [
                {
                    "cid": "cid1",
                    "kind": "side",
                    "title": "A chat",
                    "device": "phone-1",
                    "device_name": "Pixel",
                    "created_at": 1738000000,
                    "updated_at": 1738000000,
                    "deleted": False,
                    "archived": False,
                    "seq": 1,
                    "last_seq": self.pages,
                    "message_count": self.pages,
                    "unread": 0,
                }
            ],
            "deleted": [],
            "cursor": self.cursor,
            "complete": True,
        }

    async def messages(
        self, cid: str, after: int = 0, before: int = 0, limit: int = 100
    ) -> dict[str, Any]:
        self.message_calls.append({"after": after, "before": before, "limit": limit})
        seqs = list(range(1, self.pages + 1))
        if after:
            rest = [seq for seq in seqs if seq > after]
            rows = rest[:limit]
            more = len(rest) > limit
            has_older = False
            cursor = rows[-1] if more and rows else self.pages
        elif before:
            older = [seq for seq in seqs if seq < before]
            rows = older[-limit:]
            more = False
            has_older = len(older) > limit
            cursor = self.pages
        else:
            rows = seqs[-limit:]
            more = False
            has_older = len(seqs) > limit
            cursor = self.pages
        return {
            "account_id": "acc",
            "cid": cid,
            "messages": [
                {"cid": cid, "mid": f"m{seq:04d}", "role": "assistant", "text": f"m{seq}", "seq": seq}
                for seq in rows
            ],
            "cursor": cursor,
            "more": more,
            "has_older": has_older,
            "through_seq": self.pages,
        }

    async def read(self, cid: str, client: str, through: int) -> dict[str, Any]:
        self.reads.append(through)
        return {"cid": cid, "seen": through}


class FakeEngine:
    account_id = "acc"
    side_chats = ConversationSync.side_chats
    scope = ConversationSync.scope
    cursor = ConversationSync.cursor
    _set_cursor = ConversationSync._set_cursor
    _advance_cursor = ConversationSync._advance_cursor

    def __init__(self, client: FakeClient) -> None:
        self.client = client
        self.state: dict[str, Any] = {
            "cursors": {"main": 20, "all": 20},
            "message_cursors": {"cid1": 0, "gone": 9},
            "cids": {"t1": "cid1"},
        }
        self.thread = SimpleNamespace(id="t1", created_at=0)
        self.svc = SimpleNamespace(
            hub=SimpleNamespace(device_id="pc-self"),
            threads={"t1": self.thread},
            channels=None,
            bus=SimpleNamespace(publish=lambda _event: None),
        )
        self.applied: list[str] = []
        self.catalog = LazyCatalog(self)

    def cid_of(self, tid: str) -> str | None:
        return "cid1" if tid == "t1" else None

    def _mine(self, thread: Any) -> bool:
        return True

    def _apply_conversation(self, row: dict[str, Any]) -> None:
        pass

    def _apply_message(self, row: dict[str, Any]) -> None:
        self.applied.append(str(row.get("mid")))

    def _save(self) -> None:
        pass

    def list_rows(self) -> list[dict[str, Any]]:
        return []

    def view(self) -> dict[str, Any]:
        return {}


def engine(client: FakeClient) -> FakeEngine:
    fake = FakeEngine(client)
    fake.catalog.select("test", "t1")
    return fake


def test_catalog_cursor_never_goes_backwards() -> None:
    fake = engine(FakeClient(cursor=10, pages=1))
    asyncio.run(fake.catalog.pull())
    assert fake.state["cursors"]["all"] == 20
    assert fake.state["cursor"] == 20


def test_catalog_missing_cursor_does_not_reset_state() -> None:
    fake = engine(FakeClient(cursor=0, pages=1))
    asyncio.run(fake.catalog.pull())
    assert fake.state["cursors"]["all"] == 20


def test_long_history_is_not_truncated_at_50_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog_mod, "CATALOG_BUDGET_S", 60.0)
    monkeypatch.setattr(catalog_mod, "CATALOG_MAX_APPLIED", 10_000)
    monkeypatch.setattr(catalog_mod, "CATALOG_PAGE", 5)
    fake = engine(FakeClient(pages=305))  # 61 pages of five
    applied = asyncio.run(fake.catalog.pull())
    assert applied == 305
    assert set(fake.applied) == {f"m{i:04d}" for i in range(1, 306)}
    assert fake.client.reads and set(fake.client.reads) == {305}
    assert fake.state["pending_cids"] == []
    assert fake.state["history_before"] == {}


def test_long_history_resumes_from_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog_mod, "CATALOG_BUDGET_S", 60.0)
    monkeypatch.setattr(catalog_mod, "CATALOG_MAX_APPLIED", 10)
    monkeypatch.setattr(catalog_mod, "CATALOG_PAGE", 5)
    fake = engine(FakeClient(pages=25))
    first = asyncio.run(fake.catalog.pull())
    assert first == 10
    assert fake.state["pending_cids"] == ["cid1"]
    assert fake.state["history_before"] == {"cid1": 16}
    monkeypatch.setattr(catalog_mod, "CATALOG_MAX_APPLIED", 10_000)
    second = asyncio.run(fake.catalog.pull())
    assert second == 15
    assert fake.state["pending_cids"] == []
    assert fake.state["history_before"] == {}
    assert fake.client.reads and set(fake.client.reads) == {25}


def test_catalog_mode_clears_when_unsupported() -> None:
    fake = engine(FakeClient(fail=CloudError(404, "not_found", "no catalogue")))
    fake.state["catalog_mode"] = True
    assert asyncio.run(fake.catalog.pull()) is None
    assert fake.state["catalog_mode"] is False
    assert fake.state["pending_cids"] == []


def test_deleted_chat_cursor_is_cleaned() -> None:
    fake = engine(FakeClient(pages=1))
    asyncio.run(fake.catalog.pull())
    assert "gone" not in fake.state["message_cursors"]
