"""A streamed answer paid with a customer's key must not debit their balance."""

import httpx
import pytest
from test_allowance import make
from test_cloud import sign_up

from nanomuse_cloud.config import ModelSpec


@pytest.mark.asyncio
async def test_streaming_byok_records_usage_without_charging(monkeypatch):
    app, client, sender, _, cloud = make()
    signed = await sign_up(client, sender)
    caller = cloud.authenticate(signed["api_key"])
    spec = ModelSpec("private-model", "Private", "fixture", provider="openrouter", price_in=1, price_out=1)
    monkeypatch.setattr(cloud, "model_for", lambda *a: spec)
    monkeypatch.setattr(cloud, "byok_for", lambda *a: {
        "key": "test-key", "model": "fixture", "base_url": "https://supplier.example",
        "provider": "openrouter", "id": "test-key-id",
    })

    async def answer(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=(
            b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
            b'data: {"choices":[],"usage":{"prompt_tokens":1000,"completion_tokens":1000}}\n\n'
            b'data: [DONE]\n\n'
        ))

    original = app.state.http._transport
    app.state.http._transport = httpx.MockTransport(answer)
    try:
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer " + signed["api_key"]},
            json={"model": spec.id, "stream": True, "messages": [{"role": "user", "content": "hello"}]},
        )
        assert response.status_code == 200
        with cloud.db.tx() as c:
            row = c.execute(
                "SELECT prompt_tokens, completion_tokens, cost_uy, sale_uy FROM ledger WHERE account_id=? AND kind='chat'",
                (caller.account_id,),
            ).fetchone()
        assert row is not None
        assert (row["prompt_tokens"], row["completion_tokens"]) == (1000, 1000)
        assert (row["cost_uy"], row["sale_uy"]) == (0, 0)
    finally:
        app.state.http._transport = original
        await client.aclose()
