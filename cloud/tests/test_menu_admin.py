from __future__ import annotations

import asyncio
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
    assert rows[0]["key"] == "deepseek-v4.1-flash"
    assert rows[0]["name"] == "DeepSeek V4.1 Flash"
    assert rows[0]["cheapest_rub"] == 6
    assert {offer["direction"] for offer in rows[0]["offers"]} == {"input", "output"}
    assert any(not offer["route_eligible"] and "1017" in offer["exclusion_reason"] for offer in rows[0]["offers"])


def test_new_models_are_not_enabled_by_default(tmp_path, monkeypatch):
    from nanomuse_cloud.menu_admin import install

    path = tmp_path / "prices.json"
    path.write_text(json.dumps({"models": [{
        "key": "qwen-next",
        "name": "Qwen Next",
        "unit": "1M токенов (вход)",
        "new_model": True,
        "offers": [{"provider": "OpenRouter", "unit": "1M токенов (вход)", "rub": 1}],
    }]}), encoding="utf-8")
    monkeypatch.setenv("PROVIDER_PRICES_JSON", str(path))
    monkeypatch.setenv("MENU_SELECTION", str(tmp_path / "menu-selection.json"))

    class RouteApp:
        handlers = {}
        def get(self, *_args, **_kwargs):
            return lambda fn: (self.handlers.__setitem__(("GET", _args[0]), fn) or fn)

        def put(self, *_args, **_kwargs):
            return lambda fn: (self.handlers.__setitem__(("PUT", _args[0]), fn) or fn)

    settings = type("Settings", (), {"database": ":memory:", "models": [], "providers": {}})()
    app = RouteApp()
    install(app, settings, lambda: None)
    result = asyncio.run(app.handlers[("GET", "/v1/admin/menu")]())
    assert result["models"][0]["new_model"] is True
    assert result["models"][0]["on"] is False
