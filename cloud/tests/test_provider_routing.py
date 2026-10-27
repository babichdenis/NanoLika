from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from nanomuse_cloud.config import ModelSpec, Settings
from nanomuse_cloud.db import Database
from nanomuse_cloud.model_accounting import capture_metadata
from nanomuse_cloud.provider_routing import Offer, ProviderRouter, Route, identity, request_chat
from nanomuse_cloud.senders import LogSender
from nanomuse_cloud.service import Caller, Cloud


def setup_router(monkeypatch):
    monkeypatch.setenv("CLOUD_ROUTING_USD_RUB", "100")
    monkeypatch.setenv("CLOUD_ROUTING_ALLOW_QUANTIZED", "0")
    monkeypatch.delenv("CLOUD_ROUTING_CACHE", raising=False)
    providers = {
        n: SimpleNamespace(base=f"https://{n}.example", key=lambda: "fixture")
        for n in ("polza", "routerai", "openrouter", "aitunnel", "unknown")
    }
    s = SimpleNamespace(providers=providers, rub_per_usd=100)
    return ProviderRouter(s)


def quote(name, inp, out, **kw):
    return Offer(
        name,
        "deepseek/deepseek-v4.1-flash",
        inp,
        out,
        0.1,
        1_000_000,
        10_000,
        ("tools", "max_tokens"),
        ("text", "image"),
        time.time(),
        **kw,
    )


def routes(r, **payload):
    spec = ModelSpec("flash", "Flash", "deepseek-flash")
    body = {"model": spec.upstream, "messages": [{"role": "user", "content": "hi"}], **payload}
    return r.routes(spec, body, "https://own.example/chat/completions", {})


def test_total_request_cost_not_cheapest_input(monkeypatch):
    r = setup_router(monkeypatch)
    r.offers = {"polza": [quote("polza", 1, 1000)], "openrouter": [quote("openrouter", 3, 30)]}
    assert routes(r)[0].provider == "openrouter"
    # Long input and a very short output reverses the winner.
    assert routes(r, messages=[{"role": "user", "content": "a" * 30000}], max_tokens=1)[0].provider == "polza"


def test_1017_flash_endpoint_is_excluded_from_generic_flash(monkeypatch):
    r = setup_router(monkeypatch)
    spec = ModelSpec("flash", "Flash", "deepseek-v4.1-flash", provider="routerai")
    r.offers = {
        "routerai": [replace(quote("routerai", 11, 17), model="deepseek-v4.1-flash")],
        "openrouter": [replace(quote("openrouter", 15, 60), model="deepseek/deepseek-v4.1-flash-1017")],
    }
    body = {"model": spec.upstream, "messages": [{"role": "user", "content": "hello"}], "max_tokens": 512}

    candidates = r.routes(spec, body, "https://configured.example/v1/chat/completions", {})

    assert identity("deepseek-v4.1-flash") == identity("deepseek/deepseek-v4.1-flash-1017")
    assert {route.provider for route in candidates} == {"routerai"}
    assert candidates[0].provider == "routerai"
    all_quotes = r.routes(spec, body, "https://configured.example/v1/chat/completions", {}, include_all=True)
    assert {route.provider for route in all_quotes} == {"routerai", "openrouter"}
    excluded = next(route for route in all_quotes if route.provider == "openrouter")
    assert not excluded.routing_eligible
    assert "1017" in excluded.exclusion_reason


def test_1017_is_not_reintroduced_as_configured_openrouter_fallback(monkeypatch):
    r = setup_router(monkeypatch)
    spec = ModelSpec("flash", "Flash", "deepseek-flash", provider="openrouter")
    r.offers = {
        "openrouter": [replace(quote("openrouter", 45, 180), model="deepseek/deepseek-v4.1-flash-1017")],
    }
    body = {"model": spec.upstream, "messages": [{"role": "user", "content": "hello"}], "max_tokens": 512}

    assert r.routes(spec, body, "https://configured.example/v1/chat/completions", {}) == []


def test_provider_402_quarantine_survives_router_restart(tmp_path, monkeypatch):
    r = setup_router(monkeypatch)
    monkeypatch.setenv("CLOUD_ROUTING_CACHE", str(tmp_path / "routing.json"))
    r = ProviderRouter(r.settings)
    r.offers = {
        "openrouter": [quote("openrouter", 1, 1)],
        "polza": [quote("polza", 2, 2)],
    }
    spec = ModelSpec("flash", "Flash", "deepseek-flash")
    body = {"model": spec.upstream, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 512}
    failed_route = r.routes(spec, body, "https://configured.example/v1/chat/completions", {})[0]
    assert failed_route.provider == "openrouter"

    r.note_failure(failed_route, 402)
    restored = ProviderRouter(r.settings)

    assert restored.provider_blocked_until["openrouter"] > time.time()
    assert restored.routes(spec, body, "https://configured.example/v1/chat/completions", {})[0].provider == "polza"


def test_reported_openrouter_cost_quarantines_bad_offer_and_uses_next_supplier(monkeypatch):
    r = setup_router(monkeypatch)
    r.offers = {
        "openrouter": [quote("openrouter", 1, 1)],
        "polza": [quote("polza", 2, 2)],
    }
    spec = ModelSpec("flash", "Flash", "deepseek-v4.1-flash")
    body = {"model": spec.upstream, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 512}
    first = r.routes(spec, body, "https://configured.example/v1/chat/completions", {})
    assert first[0].provider == "openrouter"
    assert first[0].body["usage"] == {"include": True}

    observed = r.accounting(first[0], {"provider_cost_rub": 1.0}, 1, 512, 0)

    assert observed["provider_price_mismatch"] is True
    assert observed["provider_price_alert"] is True
    assert r.routes(spec, body, "https://configured.example/v1/chat/completions", {})[0].provider == "polza"


def test_openrouter_conversion_uses_fixed_base_rate(monkeypatch):
    monkeypatch.delenv("CLOUD_ROUTING_USD_RUB", raising=False)
    monkeypatch.delenv("CLOUD_ROUTING_CACHE", raising=False)
    r = ProviderRouter(SimpleNamespace(providers={}, rub_per_usd=123))
    assert r.usd_rub == 90


def test_flash_model_prices_merge_floating_and_dated_ids():
    from nanomuse_cloud.provider_prices import model_key

    assert model_key("openrouter", "deepseek/deepseek-v4.1-flash") == model_key(
        "openrouter", "deepseek/deepseek-v4.1-flash-1017"
    )
    assert model_key("openrouter", "deepseek/deepseek-v4.1-flash") == "deepseek-v4.1-flash"


def test_rate_outlier_is_excluded_even_if_one_side_is_cheap(monkeypatch):
    r = setup_router(monkeypatch)
    r.offers = {
        "polza": [quote("polza", 1, 2)],
        "openrouter": [quote("openrouter", 1, 25)],
    }
    body = {"model": "deepseek-v4.1-flash", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 512}
    routes = r.routes(ModelSpec("flash", "Flash", "deepseek-v4.1-flash"), body, "", {})
    assert [route.provider for route in routes] == ["polza"]
    all_quotes = r.routes(ModelSpec("flash", "Flash", "deepseek-v4.1-flash"), body, "", {}, include_all=True)
    rejected = next(route for route in all_quotes if route.provider == "openrouter")
    assert not rejected.routing_eligible
    assert "стоимость этого запроса" in rejected.exclusion_reason


def test_tool_schemas_are_counted_but_image_base64_is_not_text(monkeypatch):
    r = setup_router(monkeypatch)
    r.offers = {"polza": [quote("polza", 1, 1000)], "openrouter": [quote("openrouter", 3, 30)]}
    assert routes(r, max_tokens=16)[0].provider == "openrouter"
    tools = [{"type": "function", "function": {"name": "fixture", "description": "a" * 30000}}]
    assert routes(r, max_tokens=16, tools=tools)[0].provider == "polza"
    r.offers = {"openrouter": [replace(quote("openrouter", 1, 1), context=10000)]}
    image = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 1_000_000}}]}]
    assert routes(r, messages=image, max_tokens=16)[0].provider == "openrouter"


def test_no_unknown_or_different_version_or_stale_route(monkeypatch):
    r = setup_router(monkeypatch)
    r.offers = {
        "polza": [replace(quote("polza", 1, 1), updated=time.time() - 100000)],
        "openrouter": [replace(quote("openrouter", 1, 1), model="deepseek/deepseek-v4-pro")],
    }
    assert not routes(r)


def test_tools_vision_and_quantization_compatibility(monkeypatch):
    r = setup_router(monkeypatch)
    r.offers = {"polza": [quote("polza", 1, 1, endpoint="vendor/fp4")], "openrouter": [replace(quote("openrouter", 2, 2), parameters=())]}
    assert not routes(r, tools=[{"type": "function"}])
    r.allow_quantized = True
    assert routes(r, tools=[{"type": "function"}])[0].provider == "polza"
    r.offers = {"openrouter": [replace(quote("openrouter", 1, 1), modalities=("text",))]}
    assert not routes(r, messages=[{"role": "user", "content": [{"type": "image_url"}]}])


def test_provider_ids_and_parameters_are_adapted(monkeypatch):
    r = setup_router(monkeypatch)
    r.offers = {
        "aitunnel": [
            replace(
                quote("aitunnel", 1, 1),
                model="deepseek-v4.1-flash",
                canonical_model="deepseek/deepseek-v4.1-flash",
                parameters=("max_completion_tokens",),
            )
        ]
    }
    result = routes(r, max_tokens=10, provider={"order": ["expensive"]}, models=["other"])[0]
    assert result.body["model"] == "deepseek-v4.1-flash"
    assert result.body["max_completion_tokens"] == 10
    assert "max_tokens" not in result.body and "provider" not in result.body and "models" not in result.body


def test_structured_prices_use_units_not_cached_minimum(monkeypatch):
    r = setup_router(monkeypatch)
    row = {
        "id": "deepseek/deepseek-v4.1-flash-1017",
        "pricing": {"prompt": "0.0000003", "completion": "0.0000012", "input_cache_read": "0.000000006"},
    }
    q = r.parse("openrouter", {"data": [row]}, time.time())[0]
    assert q.input_rub == pytest.approx(31.65)
    assert q.output_rub == pytest.approx(126.6)
    assert q.cached_rub == pytest.approx(0.633)
    assert not r.parse("openrouter", {"data": [dict(row, id="model:free")]}, time.time())
    assert not r.parse("polza", {"data": [row]}, time.time())


def test_scheduled_prices_change_without_waiting_for_refresh(monkeypatch):
    r = setup_router(monkeypatch)
    row = {
        "id": "m",
        "pricing": {
            "prompt": "0.00000015",
            "completion": "0.0000006",
            "overrides": [{"utc_days": ["monday"], "utc_start": 100, "utc_end": 400, "prompt": "0.0000003", "completion": "0.0000012"}],
        },
    }
    q = r.parse("openrouter", {"data": [row]}, time.time())[0]
    peak = datetime(2026, 10, 26, 2, tzinfo=UTC).timestamp()
    idle = datetime(2026, 10, 26, 5, tzinfo=UTC).timestamp()
    assert q.rates(peak)[0] == pytest.approx(q.rates(idle)[0] * 2)


def test_402_temporarily_removes_provider_from_all_routes(monkeypatch):
    router = setup_router(monkeypatch)
    router.offers = {
        "openrouter": [quote("openrouter", 45, 180)],
        "polza": [quote("polza", 6, 18)],
    }
    spec = ModelSpec("flash", "Flash", "deepseek-flash")
    body = {"model": "flash", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 10}
    route = Route("openrouter", "https://openrouter.example", {}, {"model": "deepseek-flash"})

    router.note_failure(route, 402)
    selected = router.routes(spec, body, "https://fallback.example", {})

    assert all(candidate.provider != "openrouter" for candidate in selected)
    assert router.status()["balances"]["openrouter"]["error"] == "HTTP 402; temporarily disabled"
    assert router.provider_blocked_until["openrouter"] > time.time()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("failure", [402, 429, 503, "network"])
def test_both_modes_fallback_before_response_only(failure, stream):
    seen = []

    def handle(request):
        seen.append(request.url.host)
        if request.url.host == "one.example":
            if failure == "network":
                raise httpx.ConnectError("fixture", request=request)
            return httpx.Response(failure, json={"error": {"code": "provider"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
            rs = [Route(n, f"https://{n}.example/v1/chat/completions", {}, {"model": "m"}) for n in ("one", "two")]
            response, chosen = await request_chat(c, rs, stream)
            assert response.status_code == 200 and chosen.provider == "two"
            await response.aclose()

    asyncio.run(go())
    assert seen == ["one.example", "two.example"]


def test_all_network_failures_raise_http_error_not_attribute_error():
    def handle(request):
        raise httpx.ConnectError("fixture", request=request)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
            with pytest.raises(httpx.HTTPError):
                await request_chat(c, [Route("p", "https://p.example", {}, {})], False)

    asyncio.run(go())


def test_no_paid_retry_for_invalid_customer_request():
    seen = []

    def handle(request):
        seen.append(request.url.host)
        return httpx.Response(400, json={"error": {"code": "invalid_request"}})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
            rs = [Route(n, f"https://{n}.example", {}, {}) for n in ("one", "two")]
            response, _ = await request_chat(c, rs, False)
            assert response.status_code == 400

    asyncio.run(go())
    assert seen == ["one.example"]


def test_usd_and_rub_cost_are_not_mixed():
    obj = {"usage": {"cost": 0.25}}
    assert capture_metadata({}, obj) == {}
    usd = capture_metadata({}, obj, provider="openrouter", usd_rub=100, usd_fee_pct=5.5)
    assert usd["provider_cost_rub"] == pytest.approx(26.375)
    assert usd["provider_cost_usd"] == 0.25
    rub = capture_metadata({}, {"usage": {"cost_rub": 0.25}}, provider="polza", usd_rub=100)
    assert rub["provider_cost_rub"] == 0.25


def test_actual_cost_and_supplier_enter_one_ledger_debit():
    cloud = Cloud(Settings(database=":memory:", secret="fixture", rub_per_usd=100), Database(":memory:"), LogSender())
    row = cloud.db.create_account("a" * 64, "email", "a***@example.com", 0)
    caller = Caller(key_hash="a" * 16, account_id=str(row["id"]), channel="email", hint="a***", granted=0, used=0, account_created_at=0)
    spec = cloud.s.model("deepseek-v4.1-flash")
    assert spec is not None
    cloud.charge_chat(
        caller,
        spec,
        100,
        50,
        "routing-fixture",
        provider_cost_rub=0.123,
        routing_meta={"provider": "polza", "provider_cost_basis": "reported_rub"},
    )
    result = cloud.db._conn.execute("SELECT cost_uy,extra,sale_uy FROM ledger WHERE request_id='routing-fixture'").fetchone()
    assert result["cost_uy"] == cloud.s.rub_to_uy(0.123)
    assert json.loads(result["extra"])["model_accounting"]["provider"] == "polza"
    assert result["sale_uy"] != result["cost_uy"]


def test_balance_zero_excludes_provider_but_failed_check_is_not_zero(monkeypatch):
    r = setup_router(monkeypatch)
    r.offers = {"polza": [quote("polza", 1, 1)], "openrouter": [quote("openrouter", 2, 2)]}
    r.balances = {"polza": {"amount": 0, "checked": time.time(), "error": None}}
    assert routes(r)[0].provider == "openrouter"
    r.balances["polza"] = {"amount": None, "checked": time.time(), "error": "HTTP 403"}
    assert routes(r)[0].provider == "polza"


def test_balance_currencies_and_api_errors(monkeypatch):
    r = setup_router(monkeypatch)

    def handle(request):
        if request.url.host == "polza.example":
            return httpx.Response(200, json={"available": "12.34"})
        if request.url.host == "openrouter.example":
            return httpx.Response(200, json={"data": {"total_credits": 15, "total_usage": 2}})
        if request.url.host == "api.aitunnel.ru":
            return httpx.Response(200, json={"balance": 100, "budget": 20})
        return httpx.Response(403)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
            await r.refresh_balances(c)

    asyncio.run(go())
    assert r.balances["polza"]["amount"] == 12.34
    assert r.balances["openrouter"]["amount"] == 13
    assert r.balances["openrouter"]["currency"] == "USD"
    assert r.balances["openrouter"]["rub_estimate"] == 1300
    assert "fixture" not in json.dumps(r.status(include_offers=True))


@pytest.mark.parametrize("stream", [False, True])
async def test_api_routes_both_modes_and_debits_actual_supplier_once(monkeypatch, stream):
    from test_cloud import sign_up

    from nanomuse_cloud.api import create_app

    monkeypatch.setenv("CLOUD_ROUTING_USD_RUB", "100")
    providers = {n: SimpleNamespace(base=f"https://{n}.example", key=lambda: "fixture") for n in ("polza", "openrouter")}
    spec = ModelSpec("flash", "Flash", "deepseek-flash", price_in=1, price_out=4, sell_in=60, sell_out=90)
    settings = Settings(
        database=":memory:",
        secret="fixture",
        admin_token="admin",
        signup_tokens=100000,
        upstream_key="fixture",
        models=(spec,),
        providers=providers,
        public_base="http://cloud.test",
    )
    sender = LogSender()
    cloud = Cloud(settings, Database(":memory:"), sender)
    seen = []

    def handle(request):
        seen.append(request.url.host)
        if request.url.host == "polza.example":
            return httpx.Response(402, json={"error": {"code": "balance"}})
        usage = {"prompt_tokens": 10, "completion_tokens": 2, "cost": 0.001}
        if stream:
            text = "data: " + json.dumps({"choices": [{"delta": {"content": "OK"}}]}) + "\n\n"
            text += "data: " + json.dumps({"choices": [], "usage": usage}) + "\n\ndata: [DONE]\n\n"
            return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}], "usage": usage})

    app = create_app(settings, cloud, upstream_transport=httpx.MockTransport(handle))
    app.state.provider_router.offers = {"polza": [quote("polza", 1, 1)], "openrouter": [quote("openrouter", 2, 2)]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cloud.test") as client:
        data = await sign_up(client, sender, identifier="routing@mail.example")
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer " + data["api_key"]},
            json={"model": "flash", "messages": [{"role": "user", "content": "OK"}], "stream": stream, "max_tokens": 32},
        )
        assert response.status_code == 200, response.text
        assert "OK" in response.text
    records = cloud.db._conn.execute("SELECT cost_uy,extra FROM ledger WHERE kind='chat'").fetchall()
    assert len(records) == 1
    assert records[0]["cost_uy"] == cloud.s.rub_to_uy(0.001 * 100 * 1.055)
    assert json.loads(records[0]["extra"])["model_accounting"]["provider"] == "openrouter"
    assert seen == ["polza.example", "openrouter.example"]
    await app.state.http.aclose()


async def test_admin_visibility_persists_and_changes_client_catalog(monkeypatch):
    from test_cloud import sign_up

    from nanomuse_cloud.api import create_app

    settings = Settings(
        database=":memory:",
        secret="fixture",
        admin_token="admin",
        signup_tokens=100000,
        public_base="http://cloud.test",
        catalog_enabled=True,
        models=(ModelSpec("friendly", "Friendly", "deepseek/deepseek-v4.1-flash-1017"),),
    )
    sender = LogSender()
    cloud = Cloud(settings, Database(":memory:"), sender)
    app = create_app(settings, cloud)
    spec = settings.models[0]
    cloud.any_model = lambda _: True

    async def catalog_entries(*args):
        return [SimpleNamespace(id=spec.upstream, kind="chat", vision=False, verified=True)]

    app.state.catalog.get = catalog_entries
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cloud.test") as client:
        data = await sign_up(client, sender, identifier="visibility@mail.example")
        auth = {"Authorization": "Bearer " + data["api_key"]}
        before = (await client.get("/v1/models", headers=auth)).json()["data"]
        assert {spec.id, spec.upstream} <= {m["id"] for m in before}
        path = f"/v1/admin/prices/models/{spec.id}/visibility"
        assert (await client.put(path, json={"visible": False})).status_code == 401
        assert (await client.put(path, headers={"X-Admin-Token": "admin"}, json={"visible": "false"})).status_code == 422
        response = await client.put(path, headers={"X-Admin-Token": "admin"}, json={"visible": False})
        assert response.status_code == 200, response.text
        models = (await client.get("/v1/models", headers={"Authorization": "Bearer " + data["api_key"]})).json()["data"]
        assert spec.id not in {m["id"] for m in models}
        assert spec.upstream not in {m["id"] for m in models}
        # A new Cloud instance over the same persistent store sees the same operator choice.
        other = Cloud(settings, cloud.db, sender)
        assert not other.model_visible(spec)
        assert other.model_for(spec.id, spec.kind).id == spec.id  # Hiding is not revoking existing work.
        response = await client.put(path, headers={"X-Admin-Token": "admin"}, json={"visible": True})
        assert response.status_code == 200 and cloud.model_visible(spec)
    await app.state.http.aclose()


def test_proxy_public_catalog_uses_numeric_breakdown_not_from_label(monkeypatch):
    from nanomuse_cloud.provider_routing import proxy_catalog

    row = dict(
        key="openai/gpt-5-mini",
        category="text",
        contextTokens=400000,
        inputModalities=["text", "image"],
        capabilities=["functions"],
        sortPrice=1,
        breakdown=[
            dict(key="input_tokens", price=65, hint="за 1М токенов"),
            dict(key="output_tokens", price=516, hint="за 1М токенов"),
            dict(key="cache_read_input_tokens", price=6.45, hint="за 1М токенов"),
        ],
    )
    html = "<script>self.__next_f.push(" + json.dumps([1, '1a:["$","x",null,' + json.dumps({"rows": [row]}) + "]"]) + ")</script>"
    data = proxy_catalog(html)
    r = setup_router(monkeypatch)
    offer = r.parse("proxyapi", data, time.time())[0]
    assert (offer.input_rub, offer.output_rub, offer.cached_rub) == (65, 516, 6.45)
    assert "tools" in offer.parameters
    assert offer.context == 400000
    row["breakdown"].append(dict(key="reasoning_tokens", price=100, hint="за 1М токенов"))
    html = "<script>self.__next_f.push(" + json.dumps([1, json.dumps({"rows": [row]})]) + ")</script>"
    with pytest.raises(ValueError):
        proxy_catalog(html)
    with pytest.raises(ValueError):
        proxy_catalog("<html>New catalog format, input from 1 RUB</html>")


def test_proxy_refresh_intersects_key_catalog(monkeypatch):
    r = setup_router(monkeypatch)
    r.settings.providers = {"proxyapi": SimpleNamespace(base="https://proxyapi.example", key=lambda: "fixture")}
    row = dict(
        key="openai/allowed",
        category="text",
        breakdown=[dict(key="input_tokens", price=10, hint="за 1М токенов"), dict(key="output_tokens", price=20, hint="за 1М токенов")],
    )
    hidden = dict(row, key="openai/blocked")
    html = "<script>self.__next_f.push(" + json.dumps([1, json.dumps({"rows": [row, hidden]})]) + ")</script>"

    def handle(req):
        if req.url.path == "/models/browse":
            assert "authorization" not in req.headers
            return httpx.Response(200, text=html)
        if req.url.path == "/v1/models":
            assert req.headers["authorization"] == "Bearer fixture"
            return httpx.Response(200, json={"data": [{"id": "openai/allowed"}]})
        return httpx.Response(403, json={})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
            await r.refresh(c)

    asyncio.run(run())
    assert [o.model for o in r.offers["proxyapi"]] == ["openai/allowed"]
    assert r.balances["proxyapi"]["amount"] is None


def test_proxy_model_not_supported_is_retryable_but_input_errors_are_not():
    from nanomuse_cloud.provider_routing import retryable

    assert retryable(httpx.Response(400, json={"detail": "Model not supported"}))
    assert not retryable(httpx.Response(400, json={"detail": "Invalid messages"}))
