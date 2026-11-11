"""The BeatAPI tool resolves the current customer's relay client for each call."""

import pytest

from nanomuse.tools.beatapi import BeatAPI


class FakeCloud:
    def __init__(self, account):
        self.account = account
        self.calls = []

    async def _request(self, method, path, body, **kwargs):
        self.calls.append((method, path, body, kwargs))
        return {"account": self.account, "nanomuse": {"charged": 2}}


@pytest.mark.asyncio
async def test_factory_is_resolved_per_call_and_missing_client_is_refused():
    first, second = FakeCloud("first"), FakeCloud("second")
    selected = [first]
    tool = BeatAPI(cloud=lambda: selected[0])
    result = await tool.execute(action="search", query="weather")
    assert result.ok and first.calls[0][1] == "/v1/tools/search"
    selected[0] = second
    result = await tool.execute(action="run", reference="test", input={"x": 1})
    assert result.ok and second.calls[0][1] == "/v1/tools/run"
    assert len(first.calls) == 1 and len(second.calls) == 1
    selected[0] = None
    result = await tool.execute(action="search", query="weather")
    assert not result.ok and len(first.calls) == len(second.calls) == 1


@pytest.mark.asyncio
async def test_direct_cloud_client_remains_supported():
    client = FakeCloud("desktop")
    result = await BeatAPI(cloud=client).execute(action="inspect", reference="test")
    assert result.ok and client.calls[0][1] == "/v1/tools/inspect"
