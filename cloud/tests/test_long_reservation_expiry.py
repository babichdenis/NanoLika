"""Real HTTP handlers must retain money holds until provider completion or cancel."""
from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest
from test_audio import audio, client_of, signed_in, wav  # noqa: F401
from test_cloud import make_stack, sign_up


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['speech', 'transcription'])
async def test_slow_audio_retains_money_hold_until_cancel(audio, kind):  # noqa: F811
    cloud = audio['cloud']
    cloud.s = replace(cloud.s, signup_tokens=0, allowance_cny=10, rub_per_usd=0)
    clock, started = [0.0], asyncio.Event()
    cloud.in_flight.clock = lambda: clock[0]
    cloud.in_flight.ttl_s = 1

    async def provider(_request: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError('unreachable')

    audio['app'].state.http._transport = httpx.MockTransport(provider)
    async with await client_of(audio) as client:
        user, headers = await signed_in(client, audio['sender'])
        caller = cloud.authenticate(user['api_key'])
        if kind == 'speech':
            task = asyncio.create_task(client.post('/v1/audio/speech', headers=headers,
                json={'model': 'voice', 'input': 'slow audio', 'voice': 'alloy'}))
        else:
            task = asyncio.create_task(client.post('/v1/audio/transcriptions', headers=headers,
                files={'file': ('short.wav', wav(3), 'audio/wav')}, data={'model': 'ears'}))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            original = cloud.in_flight.reserved(caller.account_id)
            assert original > 0
            clock[0] = 2
            assert cloud.in_flight.count(caller.account_id) == 1
            assert cloud.in_flight.reserved(caller.account_id) == original
        finally:
            task.cancel()
            result = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert cloud.in_flight.count(caller.account_id) == 0
        assert cloud.in_flight.reserved(caller.account_id) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['image', 'video'])
async def test_slow_media_submission_retains_money_hold_until_cancel(kind):
    app, client, sender, _up, cloud = make_stack(
        signup_tokens=0, allowance_cny=10, per_minute_requests=0, rub_per_usd=0,
    )
    clock, started = [0.0], asyncio.Event()
    cloud.in_flight.clock = lambda: clock[0]
    cloud.in_flight.ttl_s = 1

    async def provider(_request: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError('unreachable')

    app.state.http._transport = httpx.MockTransport(provider)
    task = None
    try:
        user = await sign_up(client, sender, identifier='media@example.test')
        caller = cloud.authenticate(user['api_key'])
        headers = {'Authorization': 'Bearer ' + user['api_key']}
        if kind == 'image':
            path, body = '/v1/images/generations', {'model': 'qwen-image-3.0', 'prompt': 'a cat'}
        else:
            path = '/api/v1/services/aigc/video-generation/video-synthesis'
            body = {'model': 'wan2.2-i2v-flash', 'input': {'prompt': 'a cat'}}
        task = asyncio.create_task(client.post(path, headers=headers, json=body))
        await asyncio.wait_for(started.wait(), timeout=5)
        original = cloud.in_flight.reserved(caller.account_id)
        assert original > 0
        clock[0] = 2
        assert cloud.in_flight.count(caller.account_id) == 1
        assert cloud.in_flight.reserved(caller.account_id) == original
        task.cancel()
        result = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert cloud.in_flight.count(caller.account_id) == 0
        assert cloud.in_flight.reserved(caller.account_id) == 0
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await client.aclose()
        await app.state.http.aclose()
        cloud.db.close()
