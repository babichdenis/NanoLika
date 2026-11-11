"""Deterministic concurrent supplier faults: no external calls or customer ledger."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from test_provider_routing import quote, routes, setup_router
from test_route_quality import certificate, evidence

from nanomuse_cloud.provider_routing import Route, request_chat
from nanomuse_cloud.route_probe import image_fixture
from nanomuse_cloud.route_quality import StreamCheck


def chain():
    return [Route(p, f"https://{p}.example", {}, {"model": "m"}) for p in ("bad", "good")]


def image_messages():
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Что на картинке?"},
                {"type": "image_url", "image_url": {"url": image_fixture()}},
            ],
        }
    ]


def test_text_only_catalog_is_overridden_by_proven_vision_without_losing_image(tmp_path, monkeypatch):
    path = tmp_path / "quality.json"
    pair = "polza|deepseek/deepseek-v4.1-flash"
    certificate(path, {pair: evidence(text=True, vision=True)})
    monkeypatch.setenv("CLOUD_ROUTING_QUALITY_FILE", str(path))
    r = setup_router(monkeypatch)
    r.quality_required = {"deepseekflash"}
    r.offers = {"polza": [replace(quote("polza", 1, 2), modalities=("text",))]}
    messages = image_messages()
    selected = routes(r, messages=messages)
    assert len(selected) == 1 and selected[0].quality_status == "passed"
    assert selected[0].body["messages"] == messages
    # A real failed vision probe wins over a vendor claiming image support.
    certificate(path, {pair: evidence(text=True, vision=False)})
    r.offers["polza"] = [quote("polza", 1, 2)]
    assert routes(r, messages=messages) == []


def test_cached_family_alias_cannot_reintroduce_old_flash(monkeypatch):
    r = setup_router(monkeypatch)
    r.offers = {
        "polza": [replace(quote("polza", 0.01, 0.01), model="deepseek/deepseek-v4-flash", canonical_model="deepseek/deepseek-v4.1-flash")],
        "openrouter": [quote("openrouter", 2, 3)],
    }
    assert [v.provider for v in routes(r)] == ["openrouter"]


@pytest.mark.asyncio
async def test_600_concurrent_failures_return_only_the_good_answer():
    """60 in flight; 10 fault types, each exercised 60 times."""
    gate = asyncio.Semaphore(60)
    successes, failures, requests = [], [], []
    load = {"active": 0, "peak": 0}

    async def one(i):
        async with gate:
            kind = i % 10

            async def handle(request):
                load["active"] += 1
                load["peak"] = max(load["peak"], load["active"])
                try:
                    await asyncio.sleep(0.001)
                finally:
                    load["active"] -= 1
                requests.append((i, request.url.host))
                if request.url.host == "good.example":
                    return httpx.Response(200, json={"choices": [{"message": {"content": "RESULT"}}]})
                if kind == 0:
                    raise httpx.ConnectError("fixture", request=request)
                if kind == 1:
                    raise httpx.ReadTimeout("fixture", request=request)
                if kind == 2:
                    return httpx.Response(503, json={"error": "unavailable"})
                if kind == 3:
                    return httpx.Response(429, json={"error": "limited"})
                if kind == 4:
                    return httpx.Response(200, text="{broken")
                bad = [
                    {},
                    {"choices": []},
                    {"choices": [{"message": {"content": "  "}}]},
                    {"choices": [{"message": {"reasoning_content": "thinking"}}]},
                    {"choices": [{"message": {"tool_calls": [{"function": {"name": "f", "arguments": "{"}}]}}]},
                ]
                return httpx.Response(200, json=bad[kind - 5])

            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                response, route = await request_chat(
                    client,
                    chain(),
                    False,
                    on_success=lambda r, _: successes.append((i, r.provider)),
                    on_failure=lambda r, status, body, proven: failures.append((i, r.provider, proven)),
                )
                assert route.provider == "good"
                assert response.json()["choices"][0]["message"]["content"] == "RESULT"
                await response.aclose()

    await asyncio.wait_for(asyncio.gather(*(one(i) for i in range(600))), 20)
    assert len(successes) == sum(not proven for _, _, proven in failures) == 600
    assert sum(proven for _, _, proven in failures) == 120
    assert len(requests) == 1200
    assert load == {"active": 0, "peak": 60}
    assert all(p == "good" for _, p in successes)
    assert all(p == "bad" for _, p, _ in failures)


@pytest.mark.parametrize(
    "body",
    [
        "data: [DONE]\n\n",
        'data: {"choices":[]}\n\ndata: [DONE]\n\n',
        "data: {broken\n\n",
        'data: {"error":{"message":"overloaded"}}\n\n',
        'data: {"choices":42}\n\n',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":[],"function":{"name":"f"}}]}}]}\n\n',
    ],
)
@pytest.mark.asyncio
async def test_bad_sse_prelude_fails_over(body):
    successes, failed = [], []

    def handle(request):
        content = body if request.url.host == "bad.example" else 'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=content)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        response, route = await request_chat(
            client, chain(), True, on_success=lambda r, _: successes.append(r.provider), on_failure=lambda r, *_: failed.append(r.provider)
        )
        assert route.provider == "good" and not successes
        await response.aread()
    assert successes == ["good"] and failed == ["bad"]


class HangingStream(httpx.AsyncByteStream):
    def __init__(self, first=b": keepalive\n\n"):
        self.first = first
        self.closed = False

    async def __aiter__(self):
        yield self.first
        while True:
            await asyncio.sleep(0.002)
            yield b": keepalive\n\n"

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_keepalives_do_not_hide_no_answer_timeout(monkeypatch):
    monkeypatch.setattr("nanomuse_cloud.route_quality.STREAM_PREFETCH_SECONDS", 0.02)
    streams, failed = [], []

    def handle(request):
        if request.url.host == "good.example":
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n')
        streams.append(HangingStream())
        return httpx.Response(200, stream=streams[-1])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        response, route = await asyncio.wait_for(request_chat(client, chain(), True, on_failure=lambda r, *_: failed.append(r.provider)), 1)
        await response.aread()
    assert route.provider == "good" and failed == ["bad"]
    assert all(s.closed for s in streams)


@pytest.mark.asyncio
async def test_stream_that_never_finishes_times_out_without_replaying_output(monkeypatch):
    monkeypatch.setattr("nanomuse_cloud.route_quality.STREAM_COMPLETION_SECONDS", 0.03)
    stream = HangingStream(b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')
    seen, success, failed = [], [], []

    def handle(request):
        seen.append(request.url.host)
        return httpx.Response(200, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        response, _ = await request_chat(
            client, chain(), True, on_success=lambda *_: success.append(True), on_failure=lambda *_: failed.append(True)
        )
        with pytest.raises(httpx.ReadTimeout):
            await asyncio.wait_for(response.aread(), 1)
    assert stream.closed and seen == ["bad.example"] and not success and failed == [True]


def test_malformed_sse_index_is_a_supplier_failure_instead_of_application_crash():
    monitor = StreamCheck()
    with pytest.raises(httpx.ReadError):
        monitor.feed("data: " + json.dumps({"choices": [{"delta": {"tool_calls": [{"index": {"bad": True}, "function": {"name": "f"}}]}}]}))


@pytest.mark.asyncio
async def test_nonstream_supplier_hang_is_bounded_even_if_transport_keeps_waiting(monkeypatch):
    monkeypatch.setattr("nanomuse_cloud.provider_routing.CHAT_RESPONSE_SECONDS", 0.02)
    seen, failed = [], []

    async def handle(request):
        seen.append(request.url.host)
        if request.url.host == "bad.example":
            await asyncio.sleep(10)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        response, route = await asyncio.wait_for(
            request_chat(client, chain(), False, on_failure=lambda r, *_: failed.append(r.provider)), 1
        )
        await response.aclose()
    assert route.provider == "good" and seen == ["bad.example", "good.example"] and failed == ["bad"]
