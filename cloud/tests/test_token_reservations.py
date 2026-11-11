"""Known token costs must be reserved per account until a request actually ends."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx
import pytest
from test_audio import audio, client_of, signed_in, wav  # noqa: F401
from test_cloud import make_stack, sign_up

from nanomuse_cloud.config import Settings
from nanomuse_cloud.db import Database
from nanomuse_cloud.service import Caller, Cloud, CloudError, InFlight


def make_cloud() -> tuple[Cloud, Caller, Caller]:
    settings = Settings(
        database=":memory:", secret="test-only", signup_tokens=2500,
        allowance_cny=0, per_minute_requests=0, daily_cap_tokens=0,
    )
    db = Database(":memory:")
    for account in ("a", "b"):
        db.create_account("hash-" + account, "email", account, 2500, account_id=account)
    cloud = Cloud(settings, db)
    a = Caller(key_hash="key-a", account_id="a", channel="email", hint="a", granted=2500, used=0, account_created_at=0)
    b = replace(a, key_hash="key-b", account_id="b", hint="b")
    return cloud, a, b


def test_reservations_are_account_scoped_and_released_on_settle():
    cloud, a, b = make_cloud()
    try:
        cloud.check_budget(a, minimum=2000, request_id="a-first")
        assert cloud.in_flight.tokens_reserved("a") == 2000
        assert cloud.in_flight.tokens_reserved("b") == 0
        with pytest.raises(CloudError) as blocked:
            cloud.check_budget(a, minimum=2000, request_id="a-second")
        assert blocked.value.code == "out_of_tokens"
        cloud.check_budget(b, minimum=2000, request_id="b-first")
        assert cloud.in_flight.tokens_reserved("b") == 2000
        cloud.settle(a, "a-first")
        cloud.check_budget(a, minimum=2000, request_id="a-retry")
        cloud.settle(a, "a-retry")
        cloud.settle(b, "b-first")
        assert cloud.in_flight.tokens_reserved("a") == cloud.in_flight.tokens_reserved("b") == 0
    finally:
        cloud.db.close()


def test_stale_authenticated_caller_cannot_reuse_already_spent_tokens():
    cloud, a, _b = make_cloud()
    try:
        cloud.check_budget(a, minimum=2000, request_id="first")
        cloud.db.charge("a", "audio", "test", 0, 0, 2000, "first")
        cloud.settle(a, "first")
        assert a.remaining == 2500  # this caller was authenticated before the first charge
        with pytest.raises(CloudError) as blocked:
            cloud.check_budget(a, minimum=2000, request_id="stale")
        assert blocked.value.code == "out_of_tokens"
    finally:
        cloud.db.close()


def test_two_threads_cannot_admit_the_same_account_against_one_token_grant():
    cloud, a, _b = make_cloud()
    start = threading.Barrier(2)

    def admit(request_id: str) -> str:
        start.wait(timeout=5)
        try:
            cloud.check_budget(a, minimum=2000, request_id=request_id)
            return "admitted"
        except CloudError as exc:
            return exc.code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(admit, ("one", "two")))
        assert sorted(results) == ["admitted", "out_of_tokens"]
        assert cloud.in_flight.tokens_reserved("a") == 2000
    finally:
        cloud.settle(a, "one")
        cloud.settle(a, "two")
        cloud.db.close()


@pytest.mark.asyncio
async def test_token_hold_follows_expiry_active_task_and_cancel():
    clock = [0.0]
    held = InFlight(4, ttl_s=10, clock=lambda: clock[0])
    task = asyncio.create_task(asyncio.Event().wait())
    try:
        held.reserve("a", "running", 0, token_cost=2000)
        held.activate("a", "running", task)
        held.reserve("a", "abandoned", 0, token_cost=300)
        held.reserve("b", "other", 0, token_cost=700)
        clock[0] = 11
        assert held.tokens_reserved("a") == 2000
        assert held.tokens_reserved("b") == 0
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert held.tokens_reserved("a") == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_two_account_transcriptions_share_neither_tokens_nor_reservations(audio):  # noqa: F811
    cloud = audio["cloud"]
    cloud.s = replace(cloud.s, allowance_cny=0, signup_tokens=2500, per_minute_requests=0)
    first_started, both_started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    async def provider(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        first_started.set()
        if len(calls) == 2:
            both_started.set()
        await release.wait()
        return httpx.Response(200, json={"text": "слова", "duration": 120})

    audio["app"].state.http._transport = httpx.MockTransport(provider)
    async with await client_of(audio) as client:
        user_a, headers_a = await signed_in(client, audio["sender"])
        user_b = await sign_up(client, audio["sender"], identifier="other-audio@example.test")
        headers_b = {"Authorization": "Bearer " + user_b["api_key"]}
        caller_a = cloud.authenticate(user_a["api_key"])
        caller_b = cloud.authenticate(user_b["api_key"])
        form = {"files": {"file": ("two-minutes.wav", wav(120), "audio/wav")}, "data": {"model": "ears"}}
        first = asyncio.create_task(client.post("/v1/audio/transcriptions", headers=headers_a, **form))
        other = None
        try:
            await asyncio.wait_for(first_started.wait(), timeout=5)
            blocked = await asyncio.wait_for(client.post("/v1/audio/transcriptions", headers=headers_a, **form), timeout=5)
            other = asyncio.create_task(client.post("/v1/audio/transcriptions", headers=headers_b, **form))
            await asyncio.wait_for(both_started.wait(), timeout=5)
            reserved_a = cloud.in_flight.tokens_reserved(caller_a.account_id)
            reserved_b = cloud.in_flight.tokens_reserved(caller_b.account_id)
        finally:
            release.set()
            results = await asyncio.gather(first, *([other] if other else []), return_exceptions=True)
        assert blocked.status_code == 402 and blocked.json()["error"]["code"] == "out_of_tokens"
        assert reserved_a == reserved_b == 2000
        assert len(calls) == 2
        assert [result.status_code for result in results] == [200, 200]
        assert cloud.in_flight.tokens_reserved(caller_a.account_id) == cloud.in_flight.tokens_reserved(caller_b.account_id) == 0


@pytest.mark.asyncio
async def test_cancelled_transcription_releases_token_reservation(audio):  # noqa: F811
    cloud = audio["cloud"]
    cloud.s = replace(cloud.s, allowance_cny=0, signup_tokens=2500, per_minute_requests=0)
    clock = [0.0]
    cloud.in_flight.clock = lambda: clock[0]
    cloud.in_flight.ttl_s = 1
    started = asyncio.Event()
    blocker = asyncio.Event()
    calls = []

    async def provider(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) == 1:
            started.set()
            await blocker.wait()
        return httpx.Response(200, json={"text": "слова", "duration": 120})

    audio["app"].state.http._transport = httpx.MockTransport(provider)
    async with await client_of(audio) as client:
        user, headers = await signed_in(client, audio["sender"])
        caller = cloud.authenticate(user["api_key"])
        form = {"files": {"file": ("a.wav", wav(120), "audio/wav")}, "data": {"model": "ears"}}
        first = asyncio.create_task(client.post("/v1/audio/transcriptions", headers=headers, **form))
        await asyncio.wait_for(started.wait(), timeout=5)
        assert cloud.in_flight.tokens_reserved(caller.account_id) == 2000
        clock[0] = 2
        assert cloud.in_flight.tokens_reserved(caller.account_id) == 2000
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert cloud.in_flight.tokens_reserved(caller.account_id) == 0
        retry = await client.post("/v1/audio/transcriptions", headers=headers, **form)
        assert retry.status_code == 200


@pytest.mark.asyncio
async def test_provider_error_releases_token_reservation(audio):  # noqa: F811
    cloud = audio["cloud"]
    cloud.s = replace(cloud.s, allowance_cny=0, signup_tokens=2500, per_minute_requests=0)
    async with await client_of(audio) as client:
        user, headers = await signed_in(client, audio["sender"])
        caller = cloud.authenticate(user["api_key"])
        first = await client.post(
            "/v1/audio/transcriptions", headers=headers,
            files={"file": ("broken.wav", wav(120), "audio/wav")}, data={"model": "ears"},
        )
        assert first.status_code == 502
        assert cloud.in_flight.tokens_reserved(caller.account_id) == 0
        retry = await client.post(
            "/v1/audio/transcriptions", headers=headers,
            files={"file": ("good.wav", wav(120), "audio/wav")}, data={"model": "ears"},
        )
        assert retry.status_code == 200


@pytest.mark.asyncio
async def test_slow_speech_keeps_both_holds_past_ttl_then_releases_on_cancel(audio):  # noqa: F811
    cloud = audio["cloud"]
    cloud.s = replace(cloud.s, signup_tokens=2500, allowance_cny=10, rub_per_usd=0)
    clock, started = [0.0], asyncio.Event()
    cloud.in_flight.clock = lambda: clock[0]
    cloud.in_flight.ttl_s = 1

    async def provider(_request: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    audio["app"].state.http._transport = httpx.MockTransport(provider)
    async with await client_of(audio) as client:
        user, headers = await signed_in(client, audio["sender"])
        caller = cloud.authenticate(user["api_key"])
        task = asyncio.create_task(client.post(
            "/v1/audio/speech", headers=headers,
            json={"model": "voice", "input": "slow audio", "voice": "alloy"},
        ))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            assert cloud.in_flight.tokens_reserved(caller.account_id) == 1000
            assert cloud.in_flight.reserved(caller.account_id) > 0
            clock[0] = 2
            assert cloud.in_flight.tokens_reserved(caller.account_id) == 1000
            assert cloud.in_flight.reserved(caller.account_id) > 0
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert cloud.in_flight.count(caller.account_id) == 0
        assert cloud.in_flight.tokens_reserved(caller.account_id) == 0
        assert cloud.in_flight.reserved(caller.account_id) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["image", "video"])
async def test_slow_media_submission_keeps_holds_past_ttl_then_releases_on_cancel(kind):
    app, client, sender, _up, cloud = make_stack(
        signup_tokens=1_000_000, allowance_cny=10, per_minute_requests=0, rub_per_usd=0,
    )
    clock, started = [0.0], asyncio.Event()
    cloud.in_flight.clock = lambda: clock[0]
    cloud.in_flight.ttl_s = 1

    async def provider(_request: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    app.state.http._transport = httpx.MockTransport(provider)
    task = None
    try:
        user = await sign_up(client, sender, identifier="media@example.test")
        caller = cloud.authenticate(user["api_key"])
        headers = {"Authorization": "Bearer " + user["api_key"]}
        if kind == "image":
            path, body = "/v1/images/generations", {"model": "qwen-image-3.0", "prompt": "a cat"}
        else:
            path = "/api/v1/services/aigc/video-generation/video-synthesis"
            body = {"model": "wan2.2-i2v-flash", "input": {"prompt": "a cat"}}
        task = asyncio.create_task(client.post(path, headers=headers, json=body))
        await asyncio.wait_for(started.wait(), timeout=5)
        expected = cloud.in_flight.tokens_reserved(caller.account_id)
        assert expected > 0 and cloud.in_flight.reserved(caller.account_id) > 0
        clock[0] = 2
        assert cloud.in_flight.tokens_reserved(caller.account_id) == expected
        assert cloud.in_flight.reserved(caller.account_id) > 0
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert cloud.in_flight.count(caller.account_id) == 0
        assert cloud.in_flight.tokens_reserved(caller.account_id) == 0
        assert cloud.in_flight.reserved(caller.account_id) == 0
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await client.aclose()
        await app.state.http.aclose()
        cloud.db.close()
