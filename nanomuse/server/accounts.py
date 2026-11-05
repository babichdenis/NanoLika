"""One MuseService per account inside the shared cloud worker.

The cloud has one Python process and many people. A ``MuseService`` owns a data
directory, a workspace, a memory database and the account's Cloud key, so the same
service cannot serve two accounts: this class keeps a registry and builds each account
its own service, lazily and under its own lock. The data stays on disk when a service is
stopped for idleness, and the next request raises it again from the same directory.

Nothing here decides *who* the caller is: the account id comes from a verified relay
session, never from a request body.
"""

from __future__ import annotations

import asyncio
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from nanomuse.cloud import CLOUD_KEY
from nanomuse.config import Settings
from nanomuse.logger import logger
from nanomuse.vault import CredentialVault

_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


@dataclass
class _Entry:
    service: object
    used: float


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
        self._closed = False

    def data_dir(self, account_id: str) -> Path:
        safe = _SAFE.sub("-", str(account_id or "")).strip("-")[:80]
        return self.root / (safe or "account")

    def active(self) -> list[str]:
        return list(self._entries)

    def token(self, account_id: str) -> str:
        return _token_for(self.data_dir(account_id))

    async def service(self, account_id: str, cloud_key: str = "") -> object:
        """The account's service, started and kept warm; raises if it cannot start."""
        if not account_id:
            raise ValueError("an account id is required")
        lock = self._locks.setdefault(account_id, asyncio.Lock())
        async with lock:
            entry = self._entries.get(account_id)
            if entry is not None:
                entry.used = time.time()
                return entry.service
            await self._make_room()
            service = await self._build(account_id, cloud_key)
            self._entries[account_id] = _Entry(service, time.time())
            logger.info("accounts: runtime for {} is up ({} active)", account_id[:8], len(self._entries))
            return service

    async def _build(self, account_id: str, cloud_key: str) -> object:
        from nanomuse.server.service import MuseService

        data_dir = self.data_dir(account_id)
        data_dir.mkdir(parents=True, exist_ok=True)
        settings = self.base.model_copy(deep=True)
        settings.data_dir = data_dir
        settings.agent.workspace = data_dir / "workspace"
        settings.server.auth = True
        settings.server.token = _token_for(data_dir)
        settings.ensure_dirs()
        if cloud_key:
            CredentialVault(data_dir / "vault.enc", data_dir / "vault.key").set(CLOUD_KEY, cloud_key)
        service = MuseService(settings)
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

    async def _make_room(self) -> None:
        while len(self._entries) >= self.limit:
            oldest = min(self._entries, key=lambda key: self._entries[key].used)
            await self.stop(oldest)

    async def stop(self, account_id: str) -> None:
        entry = self._entries.pop(account_id, None)
        if entry is None:
            return
        try:
            await entry.service.stop()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - a shutdown must not block the registry
            logger.warning("accounts: stopping {} failed: {}", account_id[:8], exc)
        logger.info("accounts: runtime for {} is down ({} active)", account_id[:8], len(self._entries))

    async def sweep(self) -> None:
        """Stop services nobody has touched for ``idle_seconds``."""
        now = time.time()
        for account_id, entry in list(self._entries.items()):
            if now - entry.used >= self.idle_seconds:
                await self.stop(account_id)

    async def close(self) -> None:
        self._closed = True
        for account_id in list(self._entries):
            await self.stop(account_id)
