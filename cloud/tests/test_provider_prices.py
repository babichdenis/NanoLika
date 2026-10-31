from __future__ import annotations

import json

from nanomuse_cloud import provider_prices


def test_openrouter_keeps_free_models_and_model_limits(monkeypatch):
    monkeypatch.setattr(
        provider_prices,
        "get",
        lambda *_args, **_kwargs: json.dumps(
            {
                "data": [
                    {
                        "id": "vendor/free-chat:free",
                        "context_length": 128000,
                        "top_provider": {"max_completion_tokens": 8192},
                        "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
                        "supported_parameters": ["tools"],
                        "pricing": {"prompt": "0", "completion": "0"},
                    }
                ]
            }
        ),
    )

    offers = provider_prices.fetch_openrouter()

    assert len(offers) == 2
    assert all(offer["free"] is True for offer in offers)
    assert all(offer["context_length"] == 128000 for offer in offers)
    assert all(offer["max_output"] == 8192 for offer in offers)
    assert all("tools" in offer["supported_parameters"] for offer in offers)


def test_build_normalizes_flash_label_and_flags_new_catalogue_entry():
    payload = provider_prices.build(
        90,
        "fixture",
        {
            "openrouter": (
                True,
                "ok",
                [
                    {
                        "model": "deepseek/deepseek-v4.1-flash-1017",
                        "unit": "1M токенов (вход)",
                        "price": 0.15,
                        "currency": "USD",
                        "context_length": 1000000,
                    },
                ],
            ),
        },
    )

    row = payload["models"][0]
    assert row["key"] == "deepseek-v4.1-flash"
    assert row["name"] == "DeepSeek V4.1 Flash"
    assert row["new_model"] is True
    assert payload["changes"]["new_models"] == ["deepseek-v4.1-flash"]


def test_existing_model_is_not_marked_new_again():
    previous = {"models": [{"key": "deepseek-v4.1-flash", "first_seen_at": "2026-10-01T00:00:00+00:00"}]}
    payload = provider_prices.build(
        90,
        "fixture",
        {
            "openrouter": (
                True,
                "ok",
                [
                    {"model": "deepseek/deepseek-v4.1-flash", "unit": "1M токенов (вход)", "price": 1, "currency": "USD"},
                ],
            ),
        },
        previous=previous,
    )

    assert payload["models"][0]["new_model"] is False
    assert payload["changes"]["new_count"] == 0


def test_flash_revision_is_compared_as_the_same_family_and_can_be_cheapest():
    payload = provider_prices.build(
        90,
        "fixture",
        {
            "openrouter": (
                True,
                "ok",
                [
                    {"model": "deepseek/deepseek-v4.1-flash-1017", "unit": "1M токенов (вход)", "price": 0.05, "currency": "USD"},
                ],
            ),
            "routerai": (
                True,
                "ok",
                [
                    {"model": "deepseek-v4.1-flash", "unit": "1M токенов (вход)", "price": 8, "currency": "RUB"},
                ],
            ),
        },
    )

    row = payload["models"][0]
    dated = next(offer for offer in row["offers"] if offer["model"].endswith("1017"))
    assert dated["route_eligible"] is True
    assert dated["exclusion_reason"] == ""
    assert row["cheapest"] == "openrouter"
    assert row["cheapest_rub"] == 4.5


def test_jiugx_pricing_multipliers_become_dollars_per_million():
    """jiugx отдаёт множители New API, а не рубли.

    Сверено живым запросом deepseek-v4-1-flash-260910: 33 токена входа и 8
    выхода списали $0,000146 — это ровно model_ratio × 2 × group_ratio для
    входа и та же ставка × completion_ratio для выхода.
    """
    offers = provider_prices.jiugx_offers(
        {
            "group_ratio": {"default": 1.1},
            "data": [
                {
                    "model_name": "deepseek-v4-1-flash-260910",
                    "quota_type": 0,
                    "model_ratio": 1,
                    "completion_ratio": 4,
                    "model_price": 0,
                },
                {
                    "model_name": "doubao-seedream-5-0-pro-260628",
                    "quota_type": 1,
                    "model_ratio": 0,
                    "completion_ratio": 0,
                    "model_price": 0.3,
                },
            ],
        }
    )
    text = {offer["unit"]: offer for offer in offers if offer["model"] == "deepseek-v4-1-flash-260910"}
    assert text["1M токенов (вход)"]["price"] == 2.2
    assert text["1M токенов (выход)"]["price"] == 8.8
    image = next(offer for offer in offers if offer["model"] == "doubao-seedream-5-0-pro-260628")
    assert image["unit"] == "изображение"
    assert image["price"] == 0.33
