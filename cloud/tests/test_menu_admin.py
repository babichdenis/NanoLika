from __future__ import annotations

import json

from nanomuse_cloud.menu_admin import _market


def test_flash_aliases_merge_and_input_output_prices_stay_distinct(tmp_path, monkeypatch):
    path = tmp_path / "prices.json"
    path.write_text(json.dumps({"models": [
        {
            "key": "deepseek-v4.1-flash",
            "name": "deepseek/deepseek-v4.1-flash",
            "unit": "1M токенов (вход)",
            "offers": [
                {"provider": "Polza", "unit": "1M токенов (вход)", "rub": 6},
                {"provider": "Polza", "unit": "1M токенов (выход)", "rub": 2},
            ],
        },
        {
            "key": "deepseek-v4.1-flash-1017",
            "name": "deepseek/deepseek-v4.1-flash-1017",
            "unit": "1M токенов (вход)",
            "offers": [
                {"provider": "RouterAI", "unit": "1M токенов (вход)", "rub": 5},
                {"provider": "RouterAI", "unit": "1M токенов (выход)", "rub": 10},
            ],
        },
    ]}), encoding="utf-8")
    monkeypatch.setenv("PROVIDER_PRICES_JSON", str(path))

    rows = _market()

    assert len(rows) == 1
    assert rows[0]["key"] == "deepseek-v4.1-flash-1017"
    assert rows[0]["cheapest_rub"] == 5
    assert {offer["direction"] for offer in rows[0]["offers"]} == {"input", "output"}
