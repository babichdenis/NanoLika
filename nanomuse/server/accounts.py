"""One MuseService per account inside the shared cloud worker.

The cloud has one Python process and many people. A ``MuseService`` owns a data
directory, a workspace, a memory database and the account's Cloud key, so the same
service cannot serve two accounts: this class keeps a registry and builds each account
its own service, lazily and under its own lock. The data stays on disk when a service is
stopped for idleness, and the next request raises it again from the same directory.

Lifecycle, and why it is not just a dictionary of services:

* **Capacity is one number for the whole registry**, reserved under a single lock, so two
  accounts cannot both pass the limit check while a third build is still in flight.
* **A busy runtime is never evicted.** A request, a WebSocket or a running chat turn holds
  a lease; the sweep and the eviction for a new account skip those, and say "busy"
  (``CapacityError``) rather than stop somebody's work to make room.
* **A rotated Cloud key reaches the running service**: the vault the service reads and the
  relay client it already made are both updated, so a long-lived account does not keep a
  key that expired hours ago.
* **A due reminder wakes an account that was evicted**, because the reminder store is on
  disk and the scheduler lives inside the service.

Nothing here decides *who* the caller is: the account id comes from a verified relay
session, never from a request body.
"""

from __future__ import annotations

import asyncio
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from nanomuse import timeutil
from nanomuse.cloud import CLOUD_KEY
from nanomuse.config import Settings
from nanomuse.logger import logger
from nanomuse.vault import CredentialVault

_SAFE = re.compile(r"[^A-Za-z0-9_-]+")
_ACCOUNT_FILE = "account-id"


class CapacityError(RuntimeError):
    """Every runtime is busy; the worker cannot take another account right now."""


@dataclass
class _Entry:
    service: object
    used: float
    key: str = ""
    leases: int = 0


def _token_for(data_dir: Path) -> str:
    path = data_dir / "server_token"
    if path.exists():
        token = path.read_text("utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(18)
    path.write_text(token, "utf-8")
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover - windows
        pass
    return token


class AccountRuntime:
    """Lazy, bounded registry of ``MuseService`` objects keyed by account id."""

    def __init__(
        self,
        base: Settings,
        root: Path,
        *,
        limit: int = 24,
        idle_seconds: float = 900.0,
        allow_code: bool = False,
    ) -> None:
        self.base = base
        self.root = Path(root)
        self.limit = max(1, int(limit))
        self.idle_seconds = max(60.0, float(idle_seconds))
        # Arbitrary code without a real sandbox would let one account read another's files:
        # the cloud worker keeps shell/python out until a job boundary exists.
        self.allow_code = bool(allow_code)
        self._entries: dict[str, _Entry] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._capacity = asyncio.Lock()
        self._building = 0
        self._closed = False
        self._on_stop: list[Callable[[str], None]] = []

    def data_dir(self, account_id: str) -> Path:
        safe = _SAFE.sub("-", str(account_id or "")).strip("-")[:80]
        return self.root / (safe or "account")

    def active(self) -> list[str]:
        return list(self._entries)

    def on_stop(self, callback: Callable[[str], None]) -> None:
        """Called when a runtime goes down, so whoever cached its app can drop it."""
        self._on_stop.append(callback)

    def token(self, account_id: str) -> str:
        return _token_for(self.data_dir(account_id))

    # ------------------------------------------------------------------ leases

    def begin(self, account_id: str) -> None:
        """A request or a WebSocket starts: the runtime must not be swept while it runs."""
        entry = self._entries.get(account_id)
        if entry is None:
            return
        entry.leases += 1
        entry.used = time.time()

    def end(self, account_id: str) -> None:
        entry = self._entries.get(account_id)
        if entry is None:
            return
        entry.leases = max(0, entry.leases - 1)
        entry.used = time.time()

    def touch(self, account_id: str) -> None:
        """A live connection says it is still there (the sweep reads ``used``)."""
        entry = self._entries.get(account_id)
        if entry is not None:
            entry.used = time.time()

    def busy(self, account_id: str) -> bool:
        """A lease, or a chat turn still running inside the service."""
        entry = self._entries.get(account_id)
        if entry is None:
            return False
        if entry.leases > 0:
            return True
        threads = getattr(entry.service, "threads", None)
        if isinstance(threads, dict):
            for thread in threads.values():
                if getattr(thread, "busy", False):
                    return True
                inbox = getattr(thread, "inbox", None)
                if inbox is not None and hasattr(inbox, "empty") and not inbox.empty():
                    return True
        # Avatar drawing and animation continue after the HTTP request returns. Never
        # evict the account while one of its paid background operations is still live.
        studio = getattr(entry.service, "avatar", None)
        session = getattr(studio, "current", None)
        tasks = getattr(session, "tasks", ()) if session is not None else ()
        if any(not task.done() for task in tasks):
            return True
        return False

    # ------------------------------------------------------------------ the registry

    async def service(self, account_id: str, cloud_key: str = "") -> object:
        """The account's service, started and kept warm; raises if it cannot start."""
        if not account_id:
            raise ValueError("an account id is required")
        # Long-lived tasks started while building inherit this verified owner, including
        # services woken in the background rather than by an HTTP request.
        with timeutil.account_runtime(account_id):
            return await self._service(account_id, cloud_key)

    async def _service(self, account_id: str, cloud_key: str = "") -> object:
        lock = self._locks.setdefault(account_id, asyncio.Lock())
        async with lock:
            entry = self._entries.get(account_id)
            if entry is not None:
                entry.used = time.time()
                # The relay rotates the account key every few hours; a runtime that stays
                # warm must take the new one or it starts failing with an expired key.
                if cloud_key and cloud_key != entry.key:
                    self._rotate(account_id, entry, cloud_key)
                return entry.service
            await self._reserve()
            try:
                service = await self._build(account_id, cloud_key)
            finally:
                await self._release()
            self._entries[account_id] = _Entry(service, time.time(), cloud_key, 0)
            logger.info(
                "accounts: runtime for {} is up ({} active)", account_id[:8], len(self._entries)
            )
            return service

    async def _reserve(self) -> None:
        """A place for one build: evict an idle runtime, or refuse when all are busy."""
        async with self._capacity:
            if self._closed:
                raise RuntimeError("the account runtime is closed")
            while len(self._entries) + self._building >= self.limit:
                victim = self._victim()
                if victim is None:
                    raise CapacityError(
                        f"all {self.limit} runtimes are busy; try again in a moment"
                    )
                await self.stop(victim)
            self._building += 1

    async def _release(self) -> None:
        async with self._capacity:
            self._building = max(0, self._building - 1)

    def _victim(self) -> str | None:
        idle = [
            (account_id, entry.used)
            for account_id, entry in self._entries.items()
            if not self.busy(account_id)
        ]
        return min(idle, key=lambda item: item[1])[0] if idle else None

    async def _build(self, account_id: str, cloud_key: str) -> object:
        from nanomuse.server.service import MuseService

        data_dir = self.data_dir(account_id)
        data_dir.mkdir(parents=True, exist_ok=True)
        # The account id itself, for the sweeper that wakes an evicted account whose
        # reminder is due: the directory name is sanitised, the id is not.
        (data_dir / _ACCOUNT_FILE).write_text(account_id, "utf-8")
        settings = self.base.model_copy(deep=True)
        settings.data_dir = data_dir
        settings.agent.workspace = data_dir / "workspace"
        settings.server.auth = True
        settings.server.token = _token_for(data_dir)
        settings.ensure_dirs()
        if cloud_key:
            CredentialVault(data_dir / "vault.enc", data_dir / "vault.key").set(
                CLOUD_KEY, cloud_key
            )
        service = MuseService(settings)
        # Clock updates can arrive outside the creator task. Bind the trusted owner
        # before start so HubService can verify `/me` against it.
        service._runtime_account_id = account_id
        if not self.allow_code:
            for name in ("shell", "python_execute", "coding_agents"):
                service.app.tools.remove(name)
        await service.start()
        # The sync engine needs the account up front: the hub only learns it from the
        # settings file, which a fresh directory does not have yet.
        try:
            service.sync.account_changed(account_id)
        except Exception as exc:  # noqa: BLE001 - a sync that will not start must not hide the service
            logger.warning("accounts: sync for {} did not attach: {}", account_id[:8], exc)
        return service

    def _rotate(self, account_id: str, entry: _Entry, cloud_key: str) -> None:
        """Hand a new Cloud key to the vault the service reads and to its relay client."""
        entry.key = cloud_key
        service = entry.service
        try:
            vault = getattr(getattr(service, "app", None), "vault", None)
            if vault is not None:
                vault.set(CLOUD_KEY, cloud_key)
            else:
                data_dir = self.data_dir(account_id)
                CredentialVault(data_dir / "vault.enc", data_dir / "vault.key").set(
                    CLOUD_KEY, cloud_key
                )
            client = getattr(getattr(service, "hub", None), "cloud", None)
            if client is not None and hasattr(client, "api_key"):
                client.api_key = cloud_key
        except Exception as exc:  # noqa: BLE001 - a failed rotation must not take the account down
            logger.warning("accounts: rotating the key for {} failed: {}", account_id[:8], exc)
            return
        logger.info("accounts: cloud key for {} rotated", account_id[:8])

    async def stop(self, account_id: str) -> None:
        entry = self._entries.pop(account_id, None)
        if entry is None:
            return
        try:
            await entry.service.stop()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - a shutdown must not block the registry
            logger.warning("accounts: stopping {} failed: {}", account_id[:8], exc)
        for callback in self._on_stop:
            try:
                callback(account_id)
            except Exception as exc:  # noqa: BLE001 - a listener must not break the registry
                logger.warning("accounts: stop listener failed: {}", exc)
        logger.info(
            "accounts: runtime for {} is down ({} active)", account_id[:8], len(self._entries)
        )

    async def sweep(self) -> None:
        """Stop services nobody has touched for ``idle_seconds``; keep the busy ones."""
        now = time.time()
        for account_id, entry in list(self._entries.items()):
            if now - entry.used < self.idle_seconds:
                continue
            if self.busy(account_id):
                entry.used = now
                continue
            await self.stop(account_id)

    # ------------------------------------------------------------------ waking

    def stored_id(self, folder: Path) -> str:
        """The real account id of a data directory, or its name when the file is missing."""
        try:
            value = (folder / _ACCOUNT_FILE).read_text("utf-8").strip()
        except OSError:
            return folder.name
        return value or folder.name

    def due_accounts(self) -> list[str]:
        """Accounts on disk with a reminder that is due, whether their service is up or not."""
        from nanomuse.reminders import ReminderStore

        found: list[str] = []
        if not self.root.exists():
            return found
        for folder in sorted(self.root.iterdir()):
            if not folder.is_dir():
                continue
            store = None
            try:
                database = folder / "reminders.db"
                if not database.exists():
                    continue
                store = ReminderStore(database)
                if store.due():
                    found.append(self.stored_id(folder))
            except Exception as exc:  # noqa: BLE001 - one broken directory must not stop the scan
                logger.debug("accounts: reminders of {} unreadable: {}", folder.name, exc)
            finally:
                if store is not None:
                    store.close()
        return found

    async def wake_due(self) -> None:
        """Raise the service of an evicted account whose reminder has come due."""
        for account_id in self.due_accounts():
            if account_id in self._entries:
                continue
            try:
                await self.service(account_id)
                logger.info("accounts: woke {} for a due reminder", account_id[:8])
            except CapacityError:
                logger.warning("accounts: no room to wake {} for a reminder", account_id[:8])
                return
            except Exception as exc:  # noqa: BLE001 - one account that will not start is not the rest
                logger.warning("accounts: waking {} failed: {}", account_id[:8], exc)

    async def close(self) -> None:
        self._closed = True
        for account_id in list(self._entries):
            await self.stop(account_id)
