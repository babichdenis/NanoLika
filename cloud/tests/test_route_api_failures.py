"""Relay contract: supplier outages are not capability claims or customer charges."""

import httpx
import pytest
from test_allowance import make
from test_cloud import sign_up

from nanomuse_cloud.config import ModelSpec


@pytest.mark.asyncio
async def test_no_image_route_does_not_claim_deepseek_is_text_only(monkeypatch):
    app, client, sender, _, cloud = make()
    signed = await sign_up(client, sender)
    caller = cloud.authenticate(signed["api_key"])
    monkeypatch.setattr(app.state.provider_router, "routes", lambda *a, **kw: [])
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer " + signed["api_key"]},
        json={
            "model": "deepseek-v4.1-flash",
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.test/image.png"}}]}],
        },
    )
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "provider_routes_unavailable"
    assert "does not take pictures" not in response.text
    assert cloud.in_flight.count(caller.account_id) == 0
    assert cloud.db._conn.execute("SELECT count(*) FROM ledger WHERE kind='chat'").fetchone()[0] == 0
    await client.aclose()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.asyncio
async def test_bad_private_key_cannot_disable_shared_supplier(monkeypatch, stream):
    app, client, sender, _, cloud = make()
    signed = await sign_up(client, sender)
    caller = cloud.authenticate(signed["api_key"])
    spec = ModelSpec("fixture-private", "Fixture", "fixture", provider="openrouter")
    monkeypatch.setattr(cloud, "model_for", lambda *a: spec)
    monkeypatch.setattr(
        cloud,
        "byok_for",
        lambda *a: {
            "key": "fixture-private-key",
            "model": "fixture",
            "base_url": "https://supplier.example",
            "provider": "openrouter",
            "id": "fixture-key",
        },
    )
    monkeypatch.setattr(cloud, "note_provider_key", lambda *a, **kw: None)
    failures = []
    monkeypatch.setattr(app.state.provider_router, "note_failure", lambda *a: failures.append(a))

    async def refused(request):
        return httpx.Response(402, json={"error": {"message": "private key out of funds"}})

    original = app.state.http._transport
    app.state.http._transport = httpx.MockTransport(refused)
    try:
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer " + signed["api_key"]},
            json={"model": spec.id, "stream": stream, "messages": [{"role": "user", "content": "hello"}]},
        )
        assert response.status_code == (200 if stream else 502), response.text
        assert not failures
        assert not app.state.provider_router.provider_blocked_until
        assert cloud.in_flight.count(caller.account_id) == 0
        assert cloud.db._conn.execute("SELECT count(*) FROM ledger WHERE kind='chat'").fetchone()[0] == 0
    finally:
        app.state.http._transport = original
        await client.aclose()
