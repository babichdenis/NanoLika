"""Avatar cost estimation does not change another request's credentials."""

import asyncio
from types import SimpleNamespace

import pytest

from nanomuse.avatar.studio import AvatarStudio, Endpoint
from nanomuse.cloud import CloudError


@pytest.mark.asyncio
async def test_parallel_estimates_do_not_mutate_shared_hub(monkeypatch):
    seen = []
    clients = []
    barrier = asyncio.Barrier(2)

    class Relay:
        def __init__(self, base_url, api_key):
            self.base_url, self.api_key, self.closed = base_url, api_key, False
            clients.append(self)

        async def estimate(self, *_args, **_kwargs):
            await barrier.wait()
            seen.append((self.base_url, self.api_key))
            return {"affordable": True}

        async def close(self):
            self.closed = True

    monkeypatch.setattr("nanomuse.avatar.studio.CloudClient", Relay)
    studio = AvatarStudio.__new__(AvatarStudio)
    studio.svc = SimpleNamespace(hub=SimpleNamespace(cloud=SimpleNamespace(api_key="installation-key")))
    a = Endpoint("https://a.example/v1", "customer-a", "image", cloud=True)
    b = Endpoint("https://b.example/v1", "customer-b", "image", cloud=True)
    result = await asyncio.gather(studio.estimate(a), studio.estimate(b))
    assert all(item["affordable"] for item in result)
    assert set(seen) == {("https://a.example", "customer-a"), ("https://b.example", "customer-b")}
    assert studio.svc.hub.cloud.api_key == "installation-key"
    assert len(clients) == 2 and all(client.closed for client in clients)


@pytest.mark.asyncio
async def test_estimate_failure_closes_customer_client(monkeypatch):
    clients = []

    class Relay:
        def __init__(self, base_url, api_key):
            self.closed = False
            clients.append(self)

        async def estimate(self, *_args, **_kwargs):
            raise CloudError(401, "invalid_key", "expired")

        async def close(self):
            self.closed = True

    monkeypatch.setattr("nanomuse.avatar.studio.CloudClient", Relay)
    studio = AvatarStudio.__new__(AvatarStudio)
    studio.svc = SimpleNamespace(hub=SimpleNamespace(cloud=SimpleNamespace(api_key="installation-key")))
    result = await studio.estimate(Endpoint("https://a.example/v1", "expired", "image", cloud=True))
    assert result["error"]
    assert studio.svc.hub.cloud.api_key == "installation-key"
    assert clients[0].closed


@pytest.mark.asyncio
async def test_shared_studio_hides_and_refuses_legacy_session_without_account(monkeypatch):
    from nanomuse.avatar.studio import Session, StudioError

    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    studio = AvatarStudio.__new__(AvatarStudio)
    studio.svc = SimpleNamespace(profile=SimpleNamespace(avatar="private-face"))
    studio.current = Session(id="private-session", thread="private-thread", event_id="event", description="private")
    result = studio.view()
    assert not result["available"] and result["current"] is None and result["face"] is None
    for operation in (lambda: studio.cancel("private-session"), lambda: studio._session("private-session")):
        with pytest.raises(StudioError):
            operation()
    with pytest.raises(StudioError):
        await studio.redraw_moods()
    with pytest.raises(StudioError):
        await studio.estimate(Endpoint("https://cloud.example/v1", "foreign-key", "image", cloud=True))
