"""The relay's audio routes: what they charge, what they refuse and what they return.

The provider is a fake transport: the relay's own client is pointed at it, so every branch
of `/v1/audio/speech` and `/v1/audio/transcriptions` runs without a network.
"""

from __future__ import annotations

import base64
import struct

import httpx
import pytest
from test_cloud import sign_up

from nanomuse_cloud.api import create_app
from nanomuse_cloud.config import ModelSpec, ProviderSpec, Settings
from nanomuse_cloud.db import Database
from nanomuse_cloud.senders import LogSender
from nanomuse_cloud.service import Cloud


def wav(seconds: float, rate: int = 8000) -> bytes:
    """A real 16-bit mono WAV of `seconds`: the relay reads these headers to price it."""
    data = b"\x00\x00" * int(rate * seconds)
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    fmt = b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    body = b"data" + struct.pack("<I", len(data)) + data
    return header + fmt + body


@pytest.fixture()
def audio(monkeypatch):
    monkeypatch.setenv("POLZA_TEST_KEY", "provider-key")
    seen: list[dict] = []

    def provider(request: httpx.Request) -> httpx.Response:
        seen.append({"path": request.url.path, "body": request.content})
        if request.url.path.endswith("/audio/speech"):
            if b'"empty"' in request.content:
                return httpx.Response(200, content=b"")
            if b'"letter"' in request.content:
                return httpx.Response(
                    200,
                    json={"audio": base64.b64encode(b"RIFF" + b"\x00" * 40).decode(), "duration": 3.2, "contentType": "audio/wav"},
                )
            return httpx.Response(200, content=b"ID3fake-mp3", headers={"Content-Type": "audio/mpeg"})
        if request.url.path.endswith("/audio/transcriptions"):
            if b"broken" in request.content:
                return httpx.Response(500, text="no")
            if b"noduration" in request.content:
                return httpx.Response(200, json={"text": "привет"})
            return httpx.Response(200, json={"text": "привет", "duration": 3.0})
        return httpx.Response(404, text="unknown")

    models = (
        ModelSpec(
            "voice",
            "Voice",
            "voice",
            kind="audio",
            audio_kind="speech",
            price_speech=1,
            per_speech=1000,
            voice="alloy",
            provider="polza",
        ),
        ModelSpec(
            "voice-silent",
            "Voice without a default",
            "voice-silent",
            kind="audio",
            audio_kind="speech",
            price_speech=1,
            per_speech=1000,
            provider="polza",
        ),
        ModelSpec(
            "ears",
            "Ears",
            "ears",
            kind="audio",
            audio_kind="transcribe",
            price_minute=1,
            per_minute=1000,
            provider="polza",
        ),
    )
    settings = Settings(
        database=":memory:",
        secret="test-secret",
        admin_token="admin",
        public_base="http://cloud.test",
        models=models,
        providers={"polza": ProviderSpec(base="http://polza.test", key_env="POLZA_TEST_KEY")},
    )
    sender = LogSender()
    cloud = Cloud(settings, Database(":memory:"), sender)
    holder: dict = {}

    app = create_app(settings, cloud, upstream_transport=httpx.MockTransport(provider))
    holder["app"] = app
    holder["cloud"] = cloud
    holder["sender"] = sender
    holder["seen"] = seen
    return holder


async def client_of(holder):
    transport = httpx.ASGITransport(app=holder["app"])
    return httpx.AsyncClient(transport=transport, base_url="http://cloud.test")


async def signed_in(client, sender) -> dict:
    user = await sign_up(client, sender, identifier="audio@example.test")
    return user, {"Authorization": "Bearer " + user["api_key"]}


@pytest.mark.asyncio
async def test_speech_charges_by_hundreds_of_characters(audio):
    async with await client_of(audio) as client:
        user, headers = await signed_in(client, audio["sender"])
        response = await client.post(
            "/v1/audio/speech",
            headers=headers,
            json={"model": "voice", "input": "x" * 40, "voice": "alloy"},
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("audio/mpeg")
        # 40 characters are one unit of 100, so 1/10 of the thousand-character charge
        assert response.headers["x-nanomuse-charged"] == "100"
        assert response.headers["x-nanomuse-characters"] == "40"
        assert audio["seen"][-1]["path"] == "/v1/audio/speech"


@pytest.mark.asyncio
async def test_speech_accepts_the_json_envelope_and_reports_seconds(audio):
    async with await client_of(audio) as client:
        _user, headers = await signed_in(client, audio["sender"])
        response = await client.post(
            "/v1/audio/speech",
            headers=headers,
            json={"model": "voice", "input": "letter", "voice": "alloy"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("audio/wav")
        assert response.headers["x-nanomuse-seconds"] == "3.20"


@pytest.mark.asyncio
async def test_speech_refuses_empty_sound_from_the_provider(audio):
    async with await client_of(audio) as client:
        _user, headers = await signed_in(client, audio["sender"])
        response = await client.post(
            "/v1/audio/speech",
            headers=headers,
            json={"model": "voice", "input": "empty", "voice": "alloy"},
        )
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "upstream"


@pytest.mark.asyncio
async def test_speech_rejects_an_unknown_format_and_a_missing_voice(audio):
    async with await client_of(audio) as client:
        _user, headers = await signed_in(client, audio["sender"])
        bad_format = await client.post(
            "/v1/audio/speech",
            headers=headers,
            json={"model": "voice", "input": "hi", "voice": "alloy", "response_format": "xml"},
        )
        assert bad_format.status_code == 400
        # a speech model without a default voice and no voice in the request
        no_voice = await client.post("/v1/audio/speech", headers=headers, json={"model": "voice-silent", "input": "hi"})
        assert no_voice.status_code == 400
        assert "voice" in no_voice.json()["error"]["message"]


@pytest.mark.asyncio
async def test_speech_cannot_transcribe(audio):
    async with await client_of(audio) as client:
        _user, headers = await signed_in(client, audio["sender"])
        response = await client.post(
            "/v1/audio/transcriptions",
            headers=headers,
            files={"file": ("a.wav", wav(1), "audio/wav")},
            data={"model": "voice"},
        )
        assert response.status_code == 400
        assert "not a model that can transcribe" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_transcribe_without_a_duration_price_is_not_a_whole_minute(audio):
    async with await client_of(audio) as client:
        _user, headers = await signed_in(client, audio["sender"])
        response = await client.post(
            "/v1/audio/transcriptions",
            headers=headers,
            files={"file": ("noduration.wav", wav(3), "audio/wav")},
            data={"model": "ears"},
        )
        assert response.status_code == 200, response.text
        # three seconds are one 5-second unit: 1/12 of the minute charge, not the minute
        assert response.headers["x-nanomuse-charged"] == "83"
        assert float(response.headers["x-nanomuse-seconds"]) == pytest.approx(3.0, abs=0.2)
        assert response.json()["nanomuse"]["seconds"] == pytest.approx(3.0, abs=0.2)


@pytest.mark.asyncio
async def test_transcribe_reports_the_provider_duration(audio):
    async with await client_of(audio) as client:
        _user, headers = await signed_in(client, audio["sender"])
        response = await client.post(
            "/v1/audio/transcriptions",
            headers=headers,
            files={"file": ("one.wav", wav(1), "audio/wav")},
            data={"model": "ears"},
        )
        assert response.status_code == 200
        assert response.headers["x-nanomuse-seconds"] == "3.00"


@pytest.mark.asyncio
async def test_transcribe_refuses_an_empty_recording(audio):
    async with await client_of(audio) as client:
        _user, headers = await signed_in(client, audio["sender"])
        response = await client.post(
            "/v1/audio/transcriptions",
            headers=headers,
            files={"file": ("a.wav", b"", "audio/wav")},
            data={"model": "ears"},
        )
        assert response.status_code == 400
        assert "empty" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_transcribe_refuses_a_recording_over_the_limit(audio):
    async with await client_of(audio) as client:
        _user, headers = await signed_in(client, audio["sender"])
        too_big = b"\x00" * (25 * 1024 * 1024 + 1)
        response = await client.post(
            "/v1/audio/transcriptions",
            headers=headers,
            files={"file": ("big.mp3", too_big, "audio/mpeg")},
            data={"model": "ears"},
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "too_large"
