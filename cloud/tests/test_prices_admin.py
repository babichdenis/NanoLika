"""Цены: страница админки, которая показывает закупку, розницу и рынок.

Проверяется то, на что опирается решение о ценах: закупка каждой модели видна в юанях
и в рублях, рублёвая цифра считается по курсу, а не берётся с потолка, розница честно
показывает «не задана», когда её нет, и рынок с черновиками приходит из документов, а не
из воздуха страницы.

Данные рынка тест держит у себя (`PRICES_DATA_DIR`), а не берёт из снимка релея: снимок
обновляется на сервере и в репозиторий не едет.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_accounts import make

from nanomuse_cloud.prices_admin import (
    _catalog_snapshot,
    _clear_unquoted_purchase_cost,
    _discovered,
    _model_kind,
)

ADMIN = {"X-Admin-Token": "admin"}
CLOUD = Path(__file__).resolve().parents[1] / "nanomuse_cloud"
CONSOLE = CLOUD / "console"

SNAPSHOT = {
    "generated_at": "2026-10-24T16:30:42+00:00",
    "usd_rub": 80.54,
    "rate_source": "cbr",
    "providers": [
        {
            "name": "polza",
            "url": "https://polza.ai/models",
            "ok": True,
            "note": "737 предложений за 77 с",
            "offers": 737,
        },
        {"name": "routerai", "url": "https://routerai.ru/models", "ok": True, "note": "19", "offers": 19},
    ],
    "models": [
        {
            "name": "deepseek/deepseek-v4.1-flash",
            "unit": "1M токенов (вход)",
            "offers": [
                {"provider": "polza", "unit": "1M токенов (вход)", "rub": 5.72},
                {"provider": "routerai", "unit": "1M токенов (вход)", "rub": 6.18},
                {"provider": "polza", "unit": "1M токенов (вход)", "rub": 0.5, "route_eligible": False},
            ],
            "cheapest_rub": 5.72,
        }
    ],
}

RETAIL = {
    "collected_at": "2026-10-24 13:28 UTC",
    "note": "цены с витрин",
    "services": {
        "bothub.chat": {
            "model": "pay-as-you-go",
            "priced": 367,
            "sample": [{"name": "Gemini 4 Argon", "text": "..."}],
            "measured": {"deepseek-v4.1-flash": {"in_rub_1m": 1.69, "out_rub_1m": 80.54}},
        },
        "chadgpt.ru": {"model": "подписка + искры", "plans": {"Мини": {"rub": 290, "sparks": 120000}}},
    },
}

DRAFTS = {
    "note": "окончательного решения нет",
    "market": {
        "retail_rub_per_1m": [280, 3000],
        "our_purchase_rub_per_1m": [8.58, 21.14],
        "note": "рынок",
    },
    "drafts": [
        {
            "id": "rates-2026-10-22",
            "date": "2026-10-22",
            "status": "действует в конфиге",
            "title": "Ставки 22.10",
            "rows": [{"what": "вход", "cost": "₽12", "price": "₽260", "ratio": "×15"}],
        }
    ],
    "open_questions": ["какая кратность норма?"],
}


@pytest.fixture()
def market(tmp_path, monkeypatch):
    """Каталог данных рынка: свой у каждого теста, без снимка релея."""
    (tmp_path / "prices.json").write_text(json.dumps(SNAPSHOT, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "retail.json").write_text(json.dumps(RETAIL, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "drafts.json").write_text(json.dumps(DRAFTS, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("PRICES_DATA_DIR", str(tmp_path))
    return tmp_path


async def test_prices_answers_with_a_working_rate(market):
    app, client, *_ = make(rub_per_usd=80.0, usd_cny=8.0, topup_fee_pct=0.0)
    r = await client.get("/v1/admin/prices", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    rate = body["rate"]
    assert rate["working"] is True
    assert rate["cny_rub"] == 10.0
    assert body["count"]["models"] == len(body["models"]) > 0
    assert body["count"]["chat"] > 0
    assert [(plan["id"], plan["discount_pct"]) for plan in body["plan_discounts"]] == [
        ("lika", 10),
        ("lika-plus", 20),
        ("team", 30),
    ]


async def test_without_a_rate_the_page_says_the_ruble_figure_is_a_reference(market):
    # RUB_PER_USD = 0 is the shipped default: the relay cannot turn yuan into rubles, and a
    # page that quietly multiplied by something would be inventing a price
    app, client, *_ = make(rub_per_usd=0.0)
    body = (await client.get("/v1/admin/prices", headers=ADMIN)).json()
    assert body["rate"]["working"] is False
    assert body["rate"]["source"]
    # the snapshot's CBR dollar rate still gives a figure to reason about, and it is labelled
    assert body["rate"]["cny_rub"] > 0
    assert "не рабочий" in body["rate"]["source"]


async def test_every_chat_model_carries_purchase_and_sale(market):
    app, client, *_ = make(rub_per_usd=80.0, usd_cny=8.0, topup_fee_pct=0.0)
    body = (await client.get("/v1/admin/prices", headers=ADMIN)).json()
    chats = [m for m in body["models"] if m["kind"] == "chat"]
    assert chats
    for row in chats:
        assert row["id"] and row["name"]
        assert set(row["cost"]) >= {"cny_in", "cny_out", "rub_in", "rub_out"}
        assert set(row["sell"]) >= {"rub_in", "rub_out", "hint"}
        assert set(row["ratio"]) == {"in", "out"}
    quoted = [m for m in chats if m.get("cost_source") == "live_provider_quote"]
    unquoted = [m for m in chats if m.get("cost_source") == "no_live_provider_quote"]
    assert all(m["ratio"]["in"] is not None for m in quoted)
    assert all(m["cost"]["rub_in"] is None and m["cost"]["rub_out"] is None for m in unquoted)
    assert all(m["ratio"] == {"in": None, "out": None} for m in unquoted)
    free = next((m for m in chats if not m["sell"]["rub_in"]), None)
    if free is not None:
        # a model with no sale rate shows no multiple rather than an invented one
        assert free["ratio"]["in"] is None
        assert free["sell"]["hint"]


async def test_a_media_model_says_how_it_is_sold(market):
    app, client, *_ = make()
    body = (await client.get("/v1/admin/prices", headers=ADMIN)).json()
    for row in body["models"]:
        if row["kind"] == "chat":
            continue
        assert row["sell"]["hint"], row["id"]


async def test_market_data_comes_from_the_documents(market):
    app, client, *_ = make()
    body = (await client.get("/v1/admin/prices", headers=ADMIN)).json()
    retail = body["retail"]
    assert retail["collected_at"]
    assert set(retail["services"]) == {"bothub.chat", "chadgpt.ru"}
    # a vendor's hundreds of rows stay out of the browser: the count and a sample are enough
    assert retail["services"]["bothub.chat"]["priced"] == 367
    assert len(retail["services"]["bothub.chat"]["sample"]) <= 3
    assert retail["services"]["bothub.chat"]["measured"][0]["name"] == "deepseek-v4.1-flash"
    drafts = body["drafts"]
    assert drafts["drafts"], "the preliminary price drafts are the point of the page"
    for draft in drafts["drafts"]:
        assert draft["title"] and draft["rows"]
    assert drafts["market"]["retail_rub_per_1m"]


async def test_providers_show_which_ones_are_connected(market):
    app, client, *_ = make()
    body = (await client.get("/v1/admin/prices", headers=ADMIN)).json()
    providers = body["providers"]
    assert isinstance(providers["connected"], list)
    assert [row["name"] for row in providers["snapshot"]] == ["polza", "routerai"]
    assert providers["snapshot"][0]["min_in"] == 5.72
    assert providers["snapshot"][0]["excluded_offers"] == 1
    for row in providers["snapshot"]:
        assert row["connected"] == (row["name"] in providers["connected"])
    known = {row["name"]: row for row in providers["known"]}
    assert known["bothub"]["snapshot_present"] is False
    assert known["bothub"]["routing_supported"] is False
    assert known["polza"]["routing_supported"] is True


async def test_prices_needs_the_admin_token(market):
    app, client, *_ = make()
    r = await client.get("/v1/admin/prices")
    assert r.status_code == 401


def test_the_page_and_its_files_are_in_the_console():
    for name in ("index.html", "prices.js", "prices.css"):
        assert (CONSOLE / "prices" / name).is_file(), name


def test_the_shipped_drafts_are_the_documents_they_came_from():
    drafts = json.loads((CONSOLE / "providers" / "drafts.json").read_text(encoding="utf-8"))
    ids = {d["id"] for d in drafts["drafts"]}
    assert "rates-2026-10-22" in ids, "the rates in force are one of the drafts"
    assert len(drafts["open_questions"]) >= 3
    # every draft names the day it was written, and the page shows that date
    for draft in drafts["drafts"]:
        assert draft["date"] and draft["title"] and draft["rows"]


def test_the_shipped_retail_file_is_the_compact_form():
    retail = json.loads((CONSOLE / "providers" / "retail.json").read_text(encoding="utf-8"))
    assert retail["collected_at"]
    assert len(retail["services"]) >= 10
    for name, entry in retail["services"].items():
        assert "priced" in entry, name
        assert len(entry.get("sample") or []) <= 3, name


def test_master_catalog_keeps_reference_rows_and_separates_route_evidence(tmp_path, monkeypatch):
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "generated_at": "2026-10-27T00:00:00Z",
                "models": [
                    {
                        "id": "deepseek-chat",
                        "name": "DeepSeek Chat",
                        "mode": "chat",
                        "ours": {"polza": {"rub": 1}},
                        "dev": {"provider": "deepseek", "description": "General assistant"},
                    },
                    {"id": "deepseek-coder-v2", "name": "DeepSeek Coder V2", "mode": "chat", "ours": {}, "dev": {"provider": "deepseek"}},
                    {"id": "image-live", "name": "Image Live", "mode": "image_generation", "ours": {}, "deprecated": False},
                    {"id": "old-model", "name": "Old Model", "mode": "chat", "deprecated": True},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MODEL_CATALOG_PATH", str(path))
    _catalog_snapshot.cache_clear()
    settings = SimpleNamespace(
        models=[SimpleNamespace(id="deepseek-chat", upstream="deepseek-chat")],
        providers={"polza": SimpleNamespace(key=lambda: "configured-secret")},
    )
    router = SimpleNamespace(
        offers={"polza": [SimpleNamespace(model="deepseek/deepseek-chat", canonical_model="", updated=time.time())]}, refreshed=time.time()
    )
    result = _discovered(settings, router)
    models = {row["id"]: row for row in result["models"]}
    assert result["count"] == 4
    assert result["in_menu"] == 1
    assert models["deepseek-chat"]["in_menu"] is True
    assert models["deepseek-chat"]["live_providers"] == ["polza"]
    assert models["deepseek-coder-v2"]["kind"] == "code"
    assert models["deepseek-coder-v2"]["offers"] == []
    assert models["image-live"]["kind"] == "image"
    assert models["old-model"]["deprecated"] is True
    assert _model_kind({"id": "deepseek-chat", "mode": "chat"}) == "text"
    _catalog_snapshot.cache_clear()


def test_unrouted_model_does_not_masquerade_as_a_live_supplier_quote():
    row = {
        "cost": {"cny_in": 0.28, "cny_out": 1.18, "rub_in": 3.62, "rub_out": 15.12},
        "ratio": {"in": 2.8, "out": 2.8},
    }
    _clear_unquoted_purchase_cost(row)
    assert row["cost"]["rub_in"] is None
    assert row["cost"]["rub_out"] is None
    assert row["configured_cost"]["rub_in"] == 3.62
    assert row["ratio"] == {"in": None, "out": None}
    assert row["cost_source"] == "no_live_provider_quote"
