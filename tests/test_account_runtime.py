"""The registry behind the cloud worker: capacity, leases, key rotation and wakes.

These cover the lifecycle defects found in the 2026-11-06 review: a rotated Cloud key that
never reached the running service, an eviction that stopped somebody's work, a capacity
check two accounts could both pass, and an evicted account whose reminder was lost.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from nanomuse.cloud import CLOUD_KEY
from nanomuse.config import Settings
from nanomuse.reminders import ReminderStore
from nanomuse.server.accounts import AccountRuntime, CapacityError
from nanomuse.vault import CredentialVault


class FakeService:
    def __init__(self, data_dir: Path, key: str = "") -> None:
        self.stopped = False
        self.threads: dict[str, object] = {}
        self.app = SimpleNamespace(
            vault=CredentialVault(data_dir / "vault.enc", data_dir / "vault.key")
        )
        self.hub = SimpleNamespace(cloud=SimpleNamespace(api_key=key))

    async def stop(self) -> None:
        self.stopped = True


def registry(tmp_path: Path, *, slow: bool = False, **kw: object) -> AccountRuntime:
    runtime = AccountRuntime(Settings(), tmp_path / "accounts", **kw)

    async def build(account_id: str, cloud_key: str = "") -> FakeService:
        if slow:
            await asyncio.sleep(0.05)
        data_dir = runtime.data_dir(account_id)
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "account-id").write_text(account_id, "utf-8")
        return FakeService(data_dir, cloud_key)

    runtime._build = build  # type: ignore[method-assign]
    return runtime


def test_a_rotated_key_reaches_the_running_service(tmp_path: Path) -> None:
    runtime = registry(tmp_path)

    async def run() -> tuple[FakeService, FakeService]:
        first = await runtime.service("anna", "old-key")
        again = await runtime.service("anna", "new-key")
        return first, again  # type: ignore[return-value]

    first, again = asyncio.run(run())
    assert again is first
    assert first.app.vault.get(CLOUD_KEY) == "new-key"
    assert first.hub.cloud.api_key == "new-key"


def test_a_busy_runtime_is_not_evicted(tmp_path: Path) -> None:
    runtime = registry(tmp_path, limit=1)

    async def run() -> tuple[FakeService, FakeService]:
        first = await runtime.service("anna")
        runtime.begin("anna")
        with pytest.raises(CapacityError):
            await runtime.service("bob")
        runtime.end("anna")
        second = await runtime.service("bob")
        return first, second  # type: ignore[return-value]

    first, second = asyncio.run(run())
    assert first.stopped is True
    assert runtime.active() == ["bob"]
    assert second.stopped is False


def test_a_running_turn_keeps_the_runtime(tmp_path: Path) -> None:
    runtime = registry(tmp_path, limit=1)

    async def run() -> None:
        service = await runtime.service("anna")
        service.threads["main"] = SimpleNamespace(busy=True)  # type: ignore[attr-defined]
        with pytest.raises(CapacityError):
            await runtime.service("bob")

    asyncio.run(run())
    assert runtime.active() == ["anna"]


def test_parallel_creation_does_not_pass_the_limit(tmp_path: Path) -> None:
    runtime = registry(tmp_path, limit=1, slow=True)

    async def run() -> list[object]:
        return await asyncio.gather(
            runtime.service("anna"), runtime.service("bob"), return_exceptions=True
        )

    results = asyncio.run(run())
    assert sum(isinstance(item, CapacityError) for item in results) == 1
    assert len(runtime.active()) == 1


def test_sweep_keeps_a_busy_runtime(tmp_path: Path) -> None:
    runtime = registry(tmp_path)

    async def run() -> tuple[list[str], list[str]]:
        await runtime.service("anna")
        runtime.begin("anna")
        runtime._entries["anna"].used = time.time() - 10_000
        await runtime.sweep()
        while_busy = runtime.active()
        runtime.end("anna")
        runtime._entries["anna"].used = time.time() - 10_000
        await runtime.sweep()
        return while_busy, runtime.active()

    while_busy, after = asyncio.run(run())
    assert while_busy == ["anna"]
    assert after == []


def test_a_due_reminder_wakes_an_evicted_account(tmp_path: Path) -> None:
    runtime = registry(tmp_path)
    folder = runtime.data_dir("anna")
    folder.mkdir(parents=True)
    (folder / "account-id").write_text("anna", "utf-8")
    store = ReminderStore(folder / "reminders.db")
    store.create("позвонить маме", at="2099-01-01 10:00")
    # `create` refuses a past time; the row is moved back here to stand for one that came due
    store._conn.execute("UPDATE reminders SET next_at = '2000-01-01 00:00'")
    store._conn.commit()
    store.close()

    async def run() -> None:
        await runtime.wake_due()

    asyncio.run(run())
    assert runtime.active() == ["anna"]


def test_no_wake_without_a_due_reminder(tmp_path: Path) -> None:
    runtime = registry(tmp_path)
    folder = runtime.data_dir("anna")
    folder.mkdir(parents=True)
    (folder / "account-id").write_text("anna", "utf-8")
    ReminderStore(folder / "reminders.db").close()

    async def run() -> None:
        await runtime.wake_due()

    asyncio.run(run())
    assert runtime.active() == []


def test_the_app_cache_follows_the_service(tmp_path: Path) -> None:
    from nanomuse.server.cloud_worker import AppCache

    class Dummy:
        pass

    made: list[object] = []
    cache = AppCache(lambda service: made.append(service) or object())
    first = Dummy()
    second = Dummy()
    one = cache.app_for("anna", first)
    assert cache.app_for("anna", first) is one
    two = cache.app_for("anna", second)
    assert two is not one
    assert made == [first, second]


def test_stopping_a_runtime_drops_its_app(tmp_path: Path) -> None:
    from nanomuse.server.cloud_worker import AppCache

    runtime = registry(tmp_path)
    apps = AppCache(lambda service: object())
    runtime.on_stop(apps.drop)

    async def run() -> None:
        service = await runtime.service("anna")
        apps.app_for("anna", service)
        await runtime.stop("anna")

    asyncio.run(run())
    assert apps._apps == {}


def test_the_worker_secret_is_compared_exactly() -> None:
    from nanomuse.server.cloud_worker import worker_allows

    assert worker_allows({}, "")
    assert worker_allows({"x-worker-secret": "s3cret"}, "s3cret")
    assert not worker_allows({"x-worker-secret": "s3cret "}, "s3cret")
    assert not worker_allows({}, "s3cret")


def test_the_reminder_scan_survives_a_broken_database(tmp_path: Path) -> None:
    runtime = registry(tmp_path)
    broken = runtime.data_dir("broken")
    broken.mkdir(parents=True)
    (broken / "account-id").write_text("broken", "utf-8")
    (broken / "reminders.db").write_bytes(b"not a database")

    async def run() -> None:
        await runtime.wake_due()

    asyncio.run(run())
    assert runtime.active() == []
