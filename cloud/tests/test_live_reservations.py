"""A long-running request keeps its budget reservation until it actually ends."""

import asyncio
import json

import httpx
import pytest
from starlette.requests import Request
from test_audit_regressions import isolated_cloud, stream_response  # noqa: F401

from nanomuse_cloud.api import create_app
from nanomuse_cloud.config import ModelSpec, Settings
from nanomuse_cloud.db import Database
from nanomuse_cloud.service import Caller, Cloud, CloudError, InFlight


@pytest.mark.asyncio
async def test_only_active_reservations_outlive_their_safety_ttl():
    clock = [0.0]
    held = InFlight(2, ttl_s=10, clock=lambda: clock[0])
    running = asyncio.create_task(asyncio.Event().wait())
    try:
        held.reserve("a", "active", 9)
        held.reserve("a", "abandoned", 5)
        held.activate("a", "active", running)
        clock[0] = 11
        assert held.count("a") == 1
        assert held.reserved("a") == 9
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert held.count("a") == 0
    finally:
        running.cancel()


@pytest.mark.asyncio
async def test_real_stream_keeps_reservation_even_past_ttl(isolated_cloud):  # noqa: F811
    cloud, caller = isolated_cloud
    clock = [0.0]
    cloud.in_flight.clock = lambda: clock[0]
    cloud.in_flight.ttl_s = 10
    cloud.in_flight.limit = 1
    app, response = await stream_response(cloud, caller)

    class SlowAnswer(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
            clock[0] = 11
            assert cloud.in_flight.count(caller.account_id) == 1
            assert cloud.in_flight.reserved(caller.account_id) > 0
            with pytest.raises(CloudError) as rejected:
                cloud.check_budget(caller, request_id="parallel")
            assert rejected.value.code == "too_many_in_flight"
            yield b'data: {"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":5}}\n\n'
            yield b'data: [DONE]\n\n'

    app.state.http._transport = httpx.MockTransport(lambda _: httpx.Response(200, stream=SlowAnswer()))
    try:
        assert cloud.in_flight.count(caller.account_id) == 1
        assert b"hello" in b"".join([chunk async for chunk in response.body_iterator])
        assert cloud.in_flight.count(caller.account_id) == 0
    finally:
        await app.state.http.aclose()


@pytest.mark.asyncio
async def test_hundred_accounts_keep_concurrent_usage_separate():
    """Synthetic in-process relay test; no real provider or production data."""
    spec = ModelSpec(id="chat", name="Chat", upstream="chat", price_in=1, price_out=4, recommended=True)
    settings = Settings(
        database=":memory:", secret="test-only", upstream_key="test-key", models=(spec,),
        signup_tokens=0, allowance_cny=0, per_minute_requests=0, daily_cap_tokens=0,
    )
    db = Database(":memory:")
    cloud = Cloud(settings, db)
    current = 0
    peak = 0

    async def fake_provider(request):
        nonlocal current, peak
        current += 1
        peak = max(peak, current)
        try:
            await asyncio.sleep(0.01)
            number = int(json.loads(request.content)["user"].split("-")[-1])
            reply = {
                "choices": [{"delta": {"content": f"answer-{number}"}}],
                "usage": {"prompt_tokens": 10 + number, "completion_tokens": 3 + number},
            }
            content = ("data: " + json.dumps(reply) + "\n\ndata: [DONE]\n\n").encode()
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=content)
        finally:
            current -= 1

    app = create_app(settings, cloud, upstream_transport=httpx.MockTransport(fake_provider))
    endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/v1/chat/completions")
    callers = []
    for i in range(100):
        account = f"account-{i}"
        db.create_account(f"hash-{i}", "email", "test", 0, account_id=account)
        callers.append(Caller(key_hash=f"key-{i}", account_id=account, channel="email", hint="test", granted=0,
                              used=0, account_created_at=0, member=True))

    async def execute(caller):
        async def receive():
            return {"type": "http.request", "body": json.dumps({
                "model": "chat", "stream": True, "messages": [{"role": "user", "content": "hello"}],
            }).encode(), "more_body": False}

        request = Request({"type": "http", "method": "POST", "path": "/v1/chat/completions", "headers": [],
                           "query_string": b"", "server": ("cloud.test", 443), "scheme": "https"}, receive)
        response = await endpoint(request, caller)
        content = b"".join([chunk async for chunk in response.body_iterator])
        assert f"answer-{caller.account_id.split('-')[-1]}".encode() in content

    try:
        await asyncio.gather(*(execute(caller) for caller in callers))
        assert peak > 1
        with db.tx() as c:
            rows = c.execute("SELECT account_id, prompt_tokens, completion_tokens FROM ledger WHERE kind='chat'").fetchall()
        assert len(rows) == 100
        assert {r["account_id"]: (r["prompt_tokens"], r["completion_tokens"]) for r in rows} == {
            f"account-{i}": (10 + i, 3 + i) for i in range(100)
        }
        assert all(cloud.in_flight.count(caller.account_id) == 0 for caller in callers)
    finally:
        await app.state.http.aclose()
        db.close()
