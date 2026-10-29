from __future__ import annotations

from pathlib import Path

from nanomuse.memory import (
    MemoryStore,
    MemoryStoreRouter,
    account_scope,
    channel_scope,
    memory_scope,
    session_scope,
)


def test_customer_memory_is_shared_across_its_sessions(tmp_path: Path) -> None:
    router = MemoryStoreRouter(MemoryStore(tmp_path / "memory.db"), tmp_path / "scoped")
    with memory_scope(account_scope("customer-a")):
        item = router.add("Prefers concise answers", category="preference")

    # The customer identity, rather than the conversation id, selects the shared facts.
    for _session in ("chat-1", "chat-2"):
        with memory_scope(account_scope("customer-a")):
            assert router.get(item.id).content == "Prefers concise answers"

    with memory_scope(account_scope("customer-b")):
        assert router.get(item.id) is None
        assert router.all() == []
    router.close()


def test_relevant_filters_owner_before_shortcut_and_fill(tmp_path: Path) -> None:
    router = MemoryStoreRouter(MemoryStore(tmp_path / "memory.db"), tmp_path / "scoped")
    with memory_scope(account_scope("customer-a")):
        router.add("Customer A likes green tea")
        router.add("Customer A works in design")

    with memory_scope(account_scope("customer-b")):
        for number in range(25):
            router.add(f"Customer B private fact {number}")

    # Both the <= limit shortcut and the > limit fill path operate on a single customer's
    # database; no recent foreign fact is available to inject.
    with memory_scope(account_scope("customer-a")):
        assert {item.content for item in router.relevant("unrelated weather", limit=20)} == {
            "Customer A likes green tea",
            "Customer A works in design",
        }
        assert all("Customer B" not in item.content for item in router.relevant("anything"))
    with memory_scope(account_scope("customer-b")):
        filled = router.relevant("unrelated weather", limit=20)
        assert len(filled) == 20 and all("Customer B" in item.content for item in filled)
    router.close()


def test_legacy_memory_remains_installation_local(tmp_path: Path) -> None:
    router = MemoryStoreRouter(MemoryStore(tmp_path / "memory.db"), tmp_path / "scoped")
    legacy = router.add("Existing local preference")

    with memory_scope(account_scope("cloud-account")):
        assert router.get(legacy.id) is None
        assert router.all() == []

    with memory_scope():
        assert router.get(legacy.id).content == "Existing local preference"
    router.close()


def test_unlinked_messenger_users_and_legacy_sessions_have_distinct_scopes() -> None:
    assert channel_scope("telegram", "100") == channel_scope("telegram", "100")
    assert channel_scope("telegram", "100") != channel_scope("max", "100")
    assert channel_scope("telegram", "100") != channel_scope("telegram", "101")
    assert session_scope("old-thread-a") != session_scope("old-thread-b")
