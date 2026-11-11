"""A task waiting for cloud history still keeps its account runtime alive."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from nanomuse.config import Settings
from nanomuse.server.accounts import AccountRuntime, CapacityError


@pytest.mark.asyncio
async def test_waiting_lease_is_not_evicted_and_finished_worker_can_be(tmp_path):
    release = asyncio.Event()
    worker = asyncio.create_task(release.wait())
    first = SimpleNamespace(threads={"chat": SimpleNamespace(busy=False, inbox=asyncio.Queue(), worker=worker)})
    second = SimpleNamespace(threads={})
    first.stopped = False
    async def stop_first():
        first.stopped = True
    async def stop_second():
        pass
    first.stop, second.stop = stop_first, stop_second
    runtime = AccountRuntime(Settings(), tmp_path, limit=1)
    async def build(account, key=""):
        return first if account == "a" else second
    runtime._build = build
    try:
        await runtime.service("a")
        runtime._entries["a"].used = time.time() - 10000
        await runtime.sweep()
        assert not first.stopped
        with pytest.raises(CapacityError):
            await runtime.service("b")
        release.set()
        await worker
        assert await runtime.service("b") is second
        assert first.stopped
    finally:
        release.set()
        await worker
        await runtime.close()
