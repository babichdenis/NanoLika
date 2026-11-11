"""Unresolved spending boundaries: mocked providers, no real paid requests.

Each strict xfail documents a desired invariant rather than blessing the current
behaviour. An XPASS needs review and removal of the corresponding marker.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest
from test_audio import audio, client_of, signed_in, wav  # noqa: F401

from nanomuse_cloud.config import ModelSpec, Settings
from nanomuse_cloud.db import Database
from nanomuse_cloud.service import Caller, Cloud, CloudError


def limited_cloud() -> tuple[Cloud, Caller]:
    settings = Settings(
        database=":memory:", secret="test-only", signup_tokens=2500,
        allowance_cny=0, per_minute_requests=0, daily_cap_tokens=0,
    )
    db = Database(":memory:")
    db.create_account("test-account-hash", "email", "test", 2500, account_id="test-account")
    cloud = Cloud(settings, db)
    caller = Caller(
        key_hash="test-key", account_id="test-account", channel="email", hint="test",
        granted=2500, used=0, account_created_at=0,
    )
    return cloud, caller


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="ASR provider duration may exceed the preflight estimate and overrun the token grant",
)
@pytest.mark.asyncio
async def test_provider_duration_cannot_overrun_the_held_grant(audio):  # noqa: F811
    cloud = audio["cloud"]
    cloud.s = replace(cloud.s, allowance_cny=0, signup_tokens=100, per_minute_requests=0)

    async def provider(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "test", "duration": 120})

    audio["app"].state.http._transport = httpx.MockTransport(provider)
    async with await client_of(audio) as client:
        user, headers = await signed_in(client, audio["sender"])
        response = await client.post(
            "/v1/audio/transcriptions", headers=headers,
            files={"file": ("five-seconds.wav", wav(5), "audio/wav")}, data={"model": "ears"},
        )
        caller = cloud.authenticate(user["api_key"])
        # A safely rejected request is also acceptable. A successful one must not
        # claim to enforce a grant while recording a spend above it.
        assert response.status_code in {402, 409, 413, 422} or (
            response.status_code == 200 and caller.used <= caller.granted
        )


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BYOK token reporting currently consumes the same grant without checking live ASR holds",
)
def test_byok_usage_cannot_overrun_a_reserved_grant():
    cloud, caller = limited_cloud()
    try:
        cloud.check_budget(caller, minimum=2000, request_id="held-asr")
        chat = ModelSpec("chat-test", "Chat", "chat-test", kind="chat")
        cloud.charge_chat(caller, chat, 2000, 0, "byok", byok=True)
        ears = ModelSpec(
            "ears-test", "Ears", "ears-test", kind="audio", audio_kind="transcribe", per_minute=1000,
        )
        cloud.charge_transcribe(caller, ears, 120, "held-asr")
        account = cloud.db.account(caller.account_id)
        assert account["used"] <= account["granted"]
    finally:
        cloud.db.close()


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="daily token cap currently ignores outstanding holds and the next request's minimum",
)
def test_daily_cap_includes_outstanding_reservations():
    cloud, caller = limited_cloud()
    try:
        cloud.s = replace(cloud.s, daily_cap_tokens=2500)
        with cloud.db.tx() as db:
            db.execute("UPDATE accounts SET granted=10000 WHERE id=?", (caller.account_id,))
        cloud.check_budget(caller, minimum=2000, request_id="first")
        try:
            cloud.check_budget(caller, minimum=2000, request_id="second")
        except CloudError as refused:
            assert refused.code == "daily_cap"
        else:
            raise AssertionError("a second request exceeded the reserved daily cap")
    finally:
        cloud.db.close()


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="ASR has no durable idempotency key for a provider outcome unknown after cancellation",
)
@pytest.mark.asyncio
async def test_retry_after_cancel_does_not_start_second_paid_asr(audio):  # noqa: F811
    cloud = audio["cloud"]
    cloud.s = replace(cloud.s, allowance_cny=0, signup_tokens=10000, per_minute_requests=0)
    started, release = asyncio.Event(), asyncio.Event()
    paid_calls: list[asyncio.Task[None]] = []

    async def paid_work() -> None:
        started.set()
        await release.wait()

    async def provider(_request: httpx.Request) -> httpx.Response:
        paid = asyncio.create_task(paid_work())
        paid_calls.append(paid)
        # A disconnected caller does not prove the provider cancelled its work.
        await asyncio.shield(paid)
        return httpx.Response(200, json={"text": "test", "duration": 5})

    audio["app"].state.http._transport = httpx.MockTransport(provider)
    async with await client_of(audio) as client:
        _user, headers = await signed_in(client, audio["sender"])
        headers["Idempotency-Key"] = "test-same-voice-operation-12345"
        form = {"files": {"file": ("voice.wav", wav(5), "audio/wav")}, "data": {"model": "ears"}}
        first = asyncio.create_task(client.post("/v1/audio/transcriptions", headers=headers, **form))
        second: asyncio.Task[httpx.Response] | None = None
        try:
            await asyncio.wait_for(started.wait(), 5)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            second = asyncio.create_task(client.post("/v1/audio/transcriptions", headers=headers, **form))
            # Let the retry reach the mocked provider, if it is not prevented.
            for _ in range(100):
                if len(paid_calls) > 1 or second.done():
                    break
                await asyncio.sleep(0.01)
            assert len(paid_calls) <= 1
        finally:
            release.set()
            await asyncio.gather(first, *paid_calls, return_exceptions=True)
            if second is not None:
                await asyncio.gather(second, return_exceptions=True)
