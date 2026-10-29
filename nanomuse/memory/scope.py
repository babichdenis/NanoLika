"""Per-customer memory routing without changing the legacy installation database."""

from __future__ import annotations

import contextvars
import hashlib
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from nanomuse.memory.store import MemoryStore

INSTALLATION_SCOPE = "installation"
_current_scope: contextvars.ContextVar[str] = contextvars.ContextVar(
    "nanomuse_memory_scope", default=INSTALLATION_SCOPE
)


def account_scope(account_id: str) -> str:
    account_id = str(account_id or "").strip()
    return f"account:{account_id}" if account_id else INSTALLATION_SCOPE


def channel_scope(channel: str, sender_id: str) -> str:
    channel = str(channel or "").strip().lower()
    sender_id = str(sender_id or "").strip()
    if not channel:
        return INSTALLATION_SCOPE
    if not sender_id:
        # Missing messenger identity must never fall back to the installation's shared
        # memory, or to a group-level memory that another participant could inherit.
        return f"channel:{channel}:anonymous:{uuid.uuid4().hex}"
    return f"channel:{channel}:{sender_id}"


def session_scope(thread_id: str) -> str:
    return f"legacy-session:{thread_id}"


@contextmanager
def memory_scope(scope: str = INSTALLATION_SCOPE) -> Iterator[None]:
    """Route memory reads and writes in this task to one trusted customer namespace."""
    value = str(scope or INSTALLATION_SCOPE).strip() or INSTALLATION_SCOPE
    token = _current_scope.set(value)
    try:
        yield
    finally:
        _current_scope.reset(token)


class MemoryStoreRouter:
    """A MemoryStore-compatible facade, opening one database per customer scope.

    The existing database remains the installation owner's store. New customer scopes get
    separate databases; they never inherit or rewrite old records without explicit migration.
    """

    def __init__(self, installation_store: MemoryStore, scope_dir: Path):
        self._installation_store = installation_store
        self._scope_dir = Path(scope_dir)
        self._stores: dict[str, MemoryStore] = {INSTALLATION_SCOPE: installation_store}
        self._index_factory: Callable[[MemoryStore], Any] | None = None
        self._lock = threading.RLock()

    @property
    def current_scope(self) -> str:
        return _current_scope.get()

    def set_index_factory(self, factory: Callable[[MemoryStore], Any] | None) -> None:
        with self._lock:
            self._index_factory = factory
            for store in self._stores.values():
                store.index = factory(store) if factory is not None else None

    def _store(self, scope: str | None = None) -> MemoryStore:
        key = str(scope or self.current_scope).strip() or INSTALLATION_SCOPE
        with self._lock:
            store = self._stores.get(key)
            if store is not None:
                return store
            digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
            self._scope_dir.mkdir(parents=True, exist_ok=True)
            store = MemoryStore(self._scope_dir / f"{digest}.sqlite3")
            if self._index_factory is not None:
                store.index = self._index_factory(store)
            self._stores[key] = store
            return store

    def close(self) -> None:
        with self._lock:
            stores = list(self._stores.values())
            self._stores.clear()
        for store in stores:
            store.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store(), name)


__all__ = [
    "INSTALLATION_SCOPE",
    "MemoryStoreRouter",
    "account_scope",
    "channel_scope",
    "memory_scope",
    "session_scope",
]
