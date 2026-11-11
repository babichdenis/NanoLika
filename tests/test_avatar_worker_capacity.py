"""A paid avatar run remains alive while other accounts use the worker."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from nanomuse.config import Settings
from nanomuse.server.accounts import AccountRuntime, CapacityError


class FakeService:
    def __init__(self, task):
        self.avatar = SimpleNamespace(current=SimpleNamespace(tasks=[task]) if task else None)
        self.threads = {}
        self.stopped = False

    async def stop(self):
        self.stopped = True
        if self.avatar.current:
            for task in self.avatar.current.tasks:
                task.cancel()


@pytest.mark.asyncio
async def test_24_avatar_runs_are_not_evicted_and_release_capacity(tmp_path):
    runtime = AccountRuntime(Settings(), tmp_path, limit=24)
    release = asyncio.Event()
    tasks = []
    services = []

    async def build(account, key=""):
        task = None
        if account != "new-account":
            task = asyncio.create_task(release.wait())
            tasks.append(task)
        service = FakeService(task)
        services.append(service)
        return service

    runtime._build = build
    try:
        for index in range(24):
            await runtime.service(f"account-{index}")
        for entry in runtime._entries.values():
            entry.used = time.time() - 10_000
        await runtime.sweep()
        assert len(runtime.active()) == 24
        assert not any(service.stopped for service in services)
        with pytest.raises(CapacityError):
            await runtime.service("new-account")
        assert not any(task.cancelled() for task in tasks)
        release.set()
        await asyncio.gather(*tasks)
        newcomer = await runtime.service("new-account")
        assert len(runtime.active()) == 24
        assert newcomer.stopped is False
        assert sum(service.stopped for service in services) == 1
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
async def test_completed_or_absent_studio_tasks_do_not_block_eviction(tmp_path):
    runtime = AccountRuntime(Settings(), tmp_path, limit=1)
    done = asyncio.create_task(asyncio.sleep(0))
    await done
    first = FakeService(done)
    second = FakeService(None)

    async def build(account, key=""):
        return first if account == "first" else second

    runtime._build = build
    await runtime.service("first")
    assert not runtime.busy("first")
    assert await runtime.service("second") is second
    assert first.stopped
    await runtime.close()
