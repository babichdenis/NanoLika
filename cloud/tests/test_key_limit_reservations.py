"""Concurrent requests must respect each API key's individual spend cap."""

import asyncio
from dataclasses import replace

import httpx
import pytest
from test_accounts import auth, make
from test_cloud import sign_up

from nanomuse_cloud.config import ModelSpec, Settings
from nanomuse_cloud.db import Database
from nanomuse_cloud.service import Caller, Cloud, CloudError, InFlight


def test_in_flight_cost_counts_against_key_but_not_other_keys():
    settings = Settings(database=":memory:", secret="test", signup_tokens=0, per_minute_requests=0,
                        rub_per_usd=100, usd_cny=10, topup_fee_pct=0)
    db = Database(":memory:")
    db.create_account("account-hash", "email", "test", 0, account_id="account")
    cloud = Cloud(settings, db)
    caller = Caller(key_hash="first-key", account_id="account", channel="email", hint="test", granted=0,
                    used=0, account_created_at=0, member=True, key_daily_limit_rub=10)
    other = replace(caller, key_hash="second-key")
    reserve = settings.rub_to_uy(6)
    try:
        cloud.check_budget(caller, request_id="first", hold_uy=reserve, key_uy=reserve)
        with pytest.raises(CloudError) as blocked:
            cloud.check_budget(caller, request_id="second", hold_uy=reserve, key_uy=reserve)
        assert blocked.value.code == "key_limit"
        cloud.check_budget(other, request_id="other", hold_uy=reserve, key_uy=reserve)
        cloud.settle(caller, "first")
        cloud.check_budget(caller, request_id="again", hold_uy=reserve, key_uy=reserve)
    finally:
        db.close()


@pytest.mark.asyncio
async def test_key_reserves_expire_or_follow_the_active_request():
    clock = [0.0]
    held = InFlight(3, ttl_s=10, clock=lambda: clock[0])
    task = asyncio.create_task(asyncio.Event().wait())
    try:
        held.reserve("account", "running", 3, "key", 6)
        held.activate("account", "running", task)
        held.reserve("account", "abandoned", 4, "key", 7)
        held.reserve("other", "isolated", 2, "key", 5)
        clock[0] = 11
        assert held.key_reserved("account", "key") == 6
        assert held.key_reserved("other", "key") == 0
        held.settle("account", "running")
        assert held.key_reserved("account", "key") == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_two_parallel_http_requests_cannot_both_pass_one_key_limit():
    model = ModelSpec(id="qwen3.8-27b", name="test", upstream="test", sell_in=1000, sell_out=1000)
    app, client, sender, _, cloud, _ = make(models=(model,), rub_per_usd=90, usd_cny=7.2, per_minute_requests=0)
    clock = [0.0]
    cloud.in_flight.clock = lambda: clock[0]
    cloud.in_flight.ttl_s = 10
    signed = await sign_up(client, sender, "parallel-key@example.com", "browser")
    created = await client.post("/v1/me/keys", json={"device": "integration", "daily_limit_rub": 10},
                                headers=auth(signed["api_key"]))
    assert created.status_code == 200
    key = created.json()["api_key"]
    started, release = asyncio.Event(), asyncio.Event()

    async def provider(request):
        started.set()
        await release.wait()
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 10}})

    original = app.state.http._transport
    app.state.http._transport = httpx.MockTransport(provider)
    body = {"model": model.id, "messages": [{"role": "user", "content": "hello"}]}
    first = asyncio.create_task(client.post("/v1/chat/completions", json=body, headers=auth(key)))
    try:
        await asyncio.wait_for(started.wait(), 5)
        clock[0] = 11  # A slow non-streaming call must keep the first key's hold.
        second = await asyncio.wait_for(client.post("/v1/chat/completions", json=body, headers=auth(key)), 2)
        assert second.status_code == 429
        assert second.json()["error"]["code"] == "key_limit"
        release.set()
        assert (await first).status_code == 200
    finally:
        release.set()
        await asyncio.gather(first, return_exceptions=True)
        app.state.http._transport = original
        await client.aclose()
