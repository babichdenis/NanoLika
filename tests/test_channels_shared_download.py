"""Each concurrent delivery owns, and cleans up, its own temporary file."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from nanomuse.channels import manager as manager_module
from nanomuse.channels.manager import ChannelManager


def make_manager() -> ChannelManager:
    manager = ChannelManager.__new__(ChannelManager)
    manager._share_allowed = lambda _url: True
    return manager


@pytest.mark.asyncio
async def test_same_share_has_independent_files_for_concurrent_deliveries(monkeypatch, tmp_path):
    monkeypatch.setattr(manager_module.tempfile, "tempdir", str(tmp_path))
    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"fixture image"))
    monkeypatch.setattr(
        manager_module.httpx, "AsyncClient", lambda *args, **kw: original_client(*args, transport=transport, **kw)
    )
    manager = make_manager()
    url = "https://storage.example.test/image.png"
    first, second = await asyncio.gather(manager._fetch_shared([url]), manager._fetch_shared([url]))
    path_a, path_b = first[0][1], second[0][1]
    assert path_a != path_b
    assert path_a.read_bytes() == path_b.read_bytes() == b"fixture image"
    path_a.unlink()
    assert path_b.read_bytes() == b"fixture image"
    path_b.unlink()


@pytest.mark.asyncio
async def test_oversized_download_is_cleaned_up(monkeypatch, tmp_path):
    monkeypatch.setattr(manager_module.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(manager_module, "SHARE_MAX_BYTES", 5)
    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"123456"))
    monkeypatch.setattr(
        manager_module.httpx, "AsyncClient", lambda *args, **kw: original_client(*args, transport=transport, **kw)
    )
    assert await make_manager()._fetch_shared(["https://storage.example.test/file.png"]) == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_failed_parallel_download_does_not_remove_the_other_copy(monkeypatch, tmp_path):
    monkeypatch.setattr(manager_module.tempfile, "tempdir", str(tmp_path))
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    class BlockingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"first"
            entered.set()
            await release.wait()
            yield b"second"

    def response(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, stream=BlockingStream())
        return httpx.Response(503)

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(response)
    monkeypatch.setattr(
        manager_module.httpx, "AsyncClient", lambda *args, **kw: original_client(*args, transport=transport, **kw)
    )
    manager = make_manager()
    url = "https://storage.example.test/file.png"
    first = asyncio.create_task(manager._fetch_shared([url]))
    await asyncio.wait_for(entered.wait(), 2)
    assert await manager._fetch_shared([url]) == []
    release.set()
    result = await first
    assert result[0][1].read_bytes() == b"firstsecond"
    result[0][1].unlink()


@pytest.mark.asyncio
async def test_cancelled_download_is_cleaned_up(monkeypatch, tmp_path):
    monkeypatch.setattr(manager_module.tempfile, "tempdir", str(tmp_path))
    entered = asyncio.Event()

    class BlockingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"partial"
            entered.set()
            await asyncio.Event().wait()

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=BlockingStream()))
    monkeypatch.setattr(
        manager_module.httpx, "AsyncClient", lambda *args, **kw: original_client(*args, transport=transport, **kw)
    )
    task = asyncio.create_task(make_manager()._fetch_shared(["https://storage.example.test/file.png"]))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert list(tmp_path.iterdir()) == []
