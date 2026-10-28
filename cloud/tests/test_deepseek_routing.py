import asyncio
import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from nanomuse_cloud.deepseek_pricing import PRICE_URL, catalog
from nanomuse_cloud.provider_routing import ProviderRouter

PAGE = """<table><tr><th>MODEL</th><th>deepseek-flash<sup>(1)</sup></th><th>deepseek-v4-pro</th></tr>
<tr><td>PRICING</td><td>1M INPUT TOKENS (CACHE HIT)</td><td>OFF-PEAK</td><td>$0.003</td><td>$0.022</td></tr>
<tr><td>PEAK</td><td>$0.006</td><td>$0.044</td></tr>
<tr><td>1M INPUT TOKENS (CACHE MISS)</td><td>OFF-PEAK</td><td>$0.15</td><td>$0.66</td></tr>
<tr><td>PEAK</td><td>$0.3</td><td>$1.32</td></tr>
<tr><td>1M OUTPUT TOKENS</td><td>OFF-PEAK</td><td>$0.6</td><td>$1.98</td></tr>
<tr><td>PEAK</td><td>$1.2</td><td>$3.96</td></tr></table>"""
MODELS = {
    "data": [
        dict(
            id="deepseek-flash",
            context_window=1048576,
            max_output_tokens=393216,
            input_modalities=["text", "image"],
            output_modalities=["text"],
        ),
        dict(id="deepseek-v4-pro", context_window=1048576, max_output_tokens=393216, input_modalities=["text"], output_modalities=["text"]),
    ]
}


def ts(value):
    return datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp()


def router(monkeypatch):
    monkeypatch.delenv("CLOUD_ROUTING_CACHE", raising=False)
    monkeypatch.delenv("CLOUD_DEEPSEEK_HOLIDAYS_FILE", raising=False)
    monkeypatch.setenv("CLOUD_ROUTING_USD_RUB", "100")
    r = ProviderRouter(
        SimpleNamespace(
            providers={
                n: SimpleNamespace(base="https://" + n + ".example", key=lambda: "fixture-only-key") for n in ["deepseek", "aitunnel"]
            }
        )
    )
    data = catalog(PAGE, MODELS, ts("2026-10-27T22:00"), 100)
    r.offers["deepseek"] = r.parse("deepseek", data, time.time())
    return r


def test_official_prices_and_live_capabilities(monkeypatch):
    r = router(monkeypatch)
    a, b = r.offers["deepseek"]
    assert a.model == "deepseek-flash" and a.source_currency == "USD" and a.fx_rub_per_unit == 100
    assert a.source_url == PRICE_URL and a.context == 1048576 and a.output_limit == 393216
    assert a.input_rub == 15 and a.output_rub == 60 and a.cached_rub == pytest.approx(0.3)
    assert a.modalities == ("text", "image") and b.modalities == ("text",)
    assert a.valid_until == ts("2027-01-01T00:00")


@pytest.mark.parametrize(
    "date,expected",
    [
        ("2026-10-27T00:59", 15),
        ("2026-10-27T01:00", 30),
        ("2026-10-27T03:59", 30),
        ("2026-10-27T04:00", 15),
        ("2026-10-27T06:00", 30),
        ("2026-10-27T09:59", 30),
        ("2026-10-27T10:00", 15),
        ("2026-10-31T02:00", 15),
        ("2026-11-01T07:00", 15),
        ("2026-01-01T02:00", 15),
        ("2026-02-16T02:00", 15),
        ("2026-02-24T02:00", 30),
        ("2026-10-05T07:00", 15),
    ],
)
def test_peak_boundaries_weekends_and_holidays(monkeypatch, date, expected):
    assert router(monkeypatch).offers["deepseek"][0].rates(ts(date))[0] == expected


@pytest.mark.parametrize(
    "page",
    [
        PAGE.replace("$0.003", "missing"),
        PAGE.replace("deepseek-flash", "unknown"),
        PAGE.replace("<td>$1.2</td>", ""),
        PAGE.replace("$0.006", "$0.0001"),
    ],
)
def test_changed_price_schema_fails_closed(page):
    with pytest.raises(ValueError):
        catalog(page, MODELS, ts("2026-10-27T22:00"), 100)


def test_calendar_must_be_verified_and_can_be_updated(monkeypatch, tmp_path):
    monkeypatch.delenv("CLOUD_DEEPSEEK_HOLIDAYS_FILE", raising=False)
    with pytest.raises(ValueError):
        catalog(PAGE, MODELS, ts("2027-01-01T02:00"), 100)
    p = tmp_path / "calendar.json"
    p.write_text(json.dumps({"2027": ["2027-01-01"]}))
    monkeypatch.setenv("CLOUD_DEEPSEEK_HOLIDAYS_FILE", str(p))
    assert catalog(PAGE, MODELS, ts("2027-01-01T02:00"), 100)["data"][0]["valid_until"] == ts("2028-01-01T00:00")


def test_direct_deepseek_survives_aggregator_quarantine_and_handles_vision_tools(monkeypatch):
    r = router(monkeypatch)
    ds = r.offers["deepseek"][0]
    r.offers["aitunnel"] = [replace(ds, provider="aitunnel", model="deepseek-v4.1-flash", input_rub=1, output_rub=1)]
    r.quarantined["aitunnel", "deepseek-v4.1-flash"] = time.time() + 1800
    body = {
        "model": "deepseek-v4.1-flash",
        "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.test/fixture.png"}}]}],
        "tools": [{"type": "function", "function": {"name": "fixture"}}],
        "max_tokens": 64,
        "enable_thinking": False,
        "reasoning_effort": "none",
    }
    routes = r.routes(SimpleNamespace(upstream="deepseek-v4.1-flash", provider="aitunnel"), body, "", {})
    assert len(routes) == 1 and routes[0].provider == "deepseek"
    assert routes[0].body["model"] == "deepseek-flash"
    assert routes[0].body["thinking"] == {"type": "disabled"}
    assert "enable_thinking" not in routes[0].body and "reasoning_effort" not in routes[0].body and "provider" not in routes[0].body
    assert body["enable_thinking"] is False


def test_price_quote_is_frozen_and_does_not_contain_credentials(monkeypatch):
    r = router(monkeypatch)
    route = r.routes(SimpleNamespace(upstream="deepseek-flash", provider="deepseek"), {"model": "deepseek-flash", "messages": []}, "", {})[
        0
    ]
    result = r.accounting(route, {}, 1000, 100, 900)
    saved = json.loads(json.dumps(result))
    r.offers["deepseek"] = [replace(r.offers["deepseek"][0], input_rub=999)]
    assert saved["price_quote"]["input_rub"] != 999
    assert saved["price_quote"]["token_rate_unit"] == "RUB per 1M tokens"
    assert saved["price_quote"]["source_url"] == PRICE_URL
    assert saved["provider_cost_basis"] == "catalog_estimate"
    assert "fixture-only-key" not in json.dumps(saved)


def test_expired_calendar_quotes_are_not_eligible(monkeypatch):
    r = router(monkeypatch)
    r.offers["deepseek"] = [replace(r.offers["deepseek"][0], valid_until=int(time.time()) - 1)]
    assert not r.routes(
        SimpleNamespace(upstream="deepseek-flash", provider="deepseek"), {"model": "deepseek-flash", "messages": []}, "", {}
    )


def test_refresh_joins_authenticated_catalog_and_public_price_page_without_leaking_key(monkeypatch):
    r = router(monkeypatch)
    seen = []

    def handle(request):
        seen.append(str(request.url))
        if request.url.host == "api-docs.deepseek.com":
            assert "authorization" not in request.headers
            return httpx.Response(200, text=PAGE)
        if request.url.path == "/models":
            assert request.headers["authorization"] == "Bearer fixture-only-key"
            return httpx.Response(200, json=MODELS)
        if request.url.path == "/user/balance":
            return httpx.Response(200, json={"is_available": True, "balance_infos": [{"currency": "USD", "total_balance": "9.94"}]})
        return httpx.Response(503)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            await r.refresh(client)

    asyncio.run(run())
    assert r.offers["deepseek"] and not r.errors.get("deepseek")
    assert PRICE_URL in seen and r.balances["deepseek"]["amount"] == 9.94


def test_direct_deepseek_does_not_silently_use_an_unpriced_fallback(monkeypatch):
    r = router(monkeypatch)
    r.offers = {}
    r.allow_unpriced = False
    assert not r.routes(
        SimpleNamespace(upstream="deepseek-flash", provider="deepseek"), {"model": "deepseek-flash", "messages": []}, "", {}
    )
