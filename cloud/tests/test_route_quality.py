from __future__ import annotations

import json
import time
from dataclasses import replace

import httpx
import pytest
from test_provider_routing import quote, routes, setup_router

from nanomuse_cloud.provider_routing import ProviderRouter, Route, request_chat
from nanomuse_cloud.route_probe import result_passes
from nanomuse_cloud.route_quality import QualityEvidence, StreamCheck, valid_chat_response


def certificate(path, rows):
    path.write_text(json.dumps({"suite_version": 1, "routes": rows}))


def evidence(**checks):
    return {"expires_at": time.time() + 3600, "checks": checks}


def test_verified_quality_before_cheap_unverified_or_failed_route(tmp_path, monkeypatch):
    path = tmp_path / "quality.json"
    certificate(
        path, {"polza|deepseek/deepseek-v4.1-flash": evidence(text=False), "openrouter|deepseek/deepseek-v4.1-flash": evidence(text=True)}
    )
    monkeypatch.setenv("CLOUD_ROUTING_QUALITY_FILE", str(path))
    r = setup_router(monkeypatch)
    r.quality_required = {"deepseekflash"}
    r.offers = {"polza": [quote("polza", 0.01, 0.01)], "openrouter": [quote("openrouter", 10, 20)]}
    selected = routes(r)
    assert [v.provider for v in selected] == ["openrouter"]
    assert selected[0].quality_status == "passed"


def test_certificate_expiry_removal_and_missing_capability_fail_closed(tmp_path):
    path = tmp_path / "quality.json"
    pair = "p|m"
    certificate(path, {pair: evidence(text=True)})
    q = QualityEvidence(str(path))
    assert q.check(pair, {"text"}, True)[0]
    assert not q.check(pair, {"tools"}, True)[0]
    certificate(path, {pair: {"expires_at": time.time() - 1, "checks": {"text": True}}})
    assert not q.check(pair, {"text"}, True)[0]
    path.unlink()
    assert not q.check(pair, {"text"}, True)[0]


def test_price_and_failure_quarantine_survive_restart(tmp_path, monkeypatch):
    r = setup_router(monkeypatch)
    monkeypatch.setenv("CLOUD_ROUTING_CACHE", str(tmp_path / "routing.json"))
    r = ProviderRouter(r.settings)
    r.offers = {"polza": [quote("polza", 1, 2)], "openrouter": [quote("openrouter", 2, 3)]}
    first = routes(r)[0]
    r.accounting(first, {"provider_cost_rub": 10}, 10, 10, 0)
    r.note_failure(replace(first, provider="openrouter"), 503)
    restored = ProviderRouter(r.settings)
    assert restored.quarantined
    assert restored.health["openrouter|deepseek/deepseek-v4.1-flash"]["blocked_until"] > time.time()
    assert restored.incidents[-1]["kind"] == "price_mismatch"
    assert not routes(restored)


@pytest.mark.parametrize("status", [0, 401, 408, 429, 500, 502, 503])
def test_general_failures_pause_route_and_success_recovers(monkeypatch, status):
    r = setup_router(monkeypatch)
    r.offers = {"polza": [quote("polza", 1, 2)], "openrouter": [quote("openrouter", 2, 3)]}
    first = routes(r)[0]
    r.note_failure(first, status)
    assert routes(r)[0].provider == "openrouter"
    before = r.health["polza|deepseek/deepseek-v4.1-flash"]["failures"]
    r.note_failure(first, status, proven=True)
    assert r.health["polza|deepseek/deepseek-v4.1-flash"]["failures"] == before
    r.note_success(first)
    assert routes(r)[0].provider == "polza"


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"error": {"code": "bad"}},
        {"choices": []},
        {"choices": [{"message": {"content": "  "}}]},
        {"choices": [{"message": {"reasoning_content": "thinking only"}}]},
        {"choices": [{"message": {"tool_calls": [{"function": {"name": "f", "arguments": "{broken"}}]}}]},
    ],
)
def test_invalid_success_body_is_not_a_success(data):
    assert not valid_chat_response(data)


@pytest.mark.asyncio
async def test_200_empty_body_fails_over_without_counting_success():
    seen, success, failed = [], [], []

    def handle(request):
        seen.append(request.url.host)
        return httpx.Response(200, json={} if len(seen) == 1 else {"choices": [{"message": {"content": "ok"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        chain = [Route(n, f"https://{n}.example", {}, {"model": "m"}) for n in ["one", "two"]]
        response, selected = await request_chat(
            http, chain, False, on_success=lambda r, v: success.append(r.provider), on_failure=lambda *args: failed.append(args[1])
        )
        assert selected.provider == "two"
        assert success == ["two"] and failed == [502]
        await response.aclose()


@pytest.mark.asyncio
async def test_stream_error_before_output_fails_over_and_success_waits_for_completion():
    successes = []

    def handle(request):
        text = (
            'data: {"error":{"message":"failed"}}\n\n'
            if request.url.host == "one.example"
            else 'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'
        )
        return httpx.Response(200, text=text)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        chain = [Route(n, f"https://{n}.example", {}, {"model": "m"}) for n in ["one", "two"]]
        response, chosen = await request_chat(http, chain, True, on_success=lambda r, v: successes.append(r.provider))
        assert chosen.provider == "two"
        assert successes == []
        assert b"ok" in await response.aread()
        assert successes == ["two"]


@pytest.mark.asyncio
async def test_stream_break_after_output_is_failure_without_replay():
    success, failed, seen = [], [], []

    def handle(request):
        seen.append(request.url.host)
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        chain = [Route(n, f"https://{n}.example", {}, {"model": "m"}) for n in ["one", "two"]]
        response, _ = await request_chat(
            http, chain, True, on_success=lambda *args: success.append(True), on_failure=lambda *args: failed.append(args[1])
        )
        with pytest.raises(httpx.ReadError):
            await response.aread()
    assert success == [] and failed == [502] and seen == ["one.example"]


def test_stream_assembles_tool_arguments_and_rejects_reasoning_only():
    monitor = StreamCheck()
    for delta in [
        {"tool_calls": [{"index": 0, "function": {"name": "f", "arguments": '{"x":'}}]},
        {"tool_calls": [{"index": 0, "function": {"arguments": "3}"}}]},
    ]:
        monitor.feed("data: " + json.dumps({"choices": [{"delta": delta}]}))
    monitor.feed("data: [DONE]")
    assert monitor.valid()
    monitor = StreamCheck()
    monitor.feed('data: {"choices":[{"delta":{"reasoning_content":"thinking"}}]}')
    monitor.feed("data: [DONE]")
    assert not monitor.valid()


def test_probe_check_does_not_accept_wrong_math_or_text_instead_of_tool_call():
    assert result_passes("json", {"choices": [{"message": {"content": '{"result":667}'}}]})
    assert not result_passes("json", {"choices": [{"message": {"content": '{"result":668}'}}]})
    assert not result_passes("tools", {"choices": [{"message": {"content": "323"}}]})


def test_legacy_function_call_remains_a_valid_answer():
    assert valid_chat_response({"choices": [{"message": {"function_call": {"name": "f", "arguments": '{"x":3}'}}}]})
    monitor = StreamCheck()
    for function in [{"name": "f", "arguments": '{"x":'}, {"arguments": "3}"}]:
        monitor.feed("data: " + json.dumps({"choices": [{"delta": {"function_call": function}}]}))
    monitor.feed("data: [DONE]")
    assert monitor.valid()


@pytest.mark.asyncio
async def test_telegram_alert_uses_existing_bot_proxy(monkeypatch):
    monkeypatch.setenv("CLOUD_PRICE_ALERT_TELEGRAM_PROXY", "http://proxy.example:8080")
    created, sent = [], []
    original = httpx.AsyncClient

    def handle(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    def client(**kwargs):
        created.append(kwargs)
        return original(transport=httpx.MockTransport(handle))

    monkeypatch.setattr(httpx, "AsyncClient", client)
    async with original(transport=httpx.MockTransport(lambda _: httpx.Response(500))) as unused:
        await ProviderRouter._send_price_alert(unused, "fixture-token", "fixture-owner", "fixture notice")
    assert created[0]["proxy"] == "http://proxy.example:8080"
    assert sent == [{"chat_id": "fixture-owner", "text": "fixture notice"}]
