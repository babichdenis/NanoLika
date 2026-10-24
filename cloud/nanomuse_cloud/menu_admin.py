"""Прилавок: что из моря моделей видит человек и как это разложено по полкам.

Оператор открывает `/app/admin/menu/`, видит весь рынок из `prices.json` с пометкой,
что из этого мусор и почему, ставит галочки на нужном, раскладывает по полкам и
сохраняет. Выбор лежит в `menu-selection.json` рядом с базой: один файл, который
читают и сборка меню, и эта страница, поэтому ничего не разъезжается.

Маршруты (оба под админским токеном):

    GET /v1/admin/menu  — рынок, пометки отсева, полки и текущий выбор
    PUT /v1/admin/menu  — сохранить выбор: {"models": {"<имя>": {"on": true, "shelves": [...]}}}

Ничего не решает за оператора: по умолчанию «на витрине» стоит всё, что прошло
отсев мусора, а дальше он снимает галочки сам.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi import Depends

from .menu_choice import UNIT_NAMES, kind_of, slug, unit_key, verdict

PRICES = Path(__file__).resolve().parent / "console" / "providers" / "prices.json"


def _prices_path() -> Path:
    import os

    return Path(os.environ.get("PROVIDER_PRICES_JSON") or PRICES)


def _selection_path(settings: Any) -> Path:
    import os

    explicit = os.environ.get("MENU_SELECTION")
    if explicit:
        return Path(explicit)
    database = str(getattr(settings, "database", "") or "")
    if database and not database.startswith((":memory:", "file:")):
        return Path(database).parent / "menu-selection.json"
    return Path("menu-selection.json")


def _market() -> list[dict]:
    """Строки таблицы цен, схлопнутые до моделей: одна модель — одна строка прилавка."""
    path = _prices_path()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    by_name: dict[str, dict] = {}
    for model in data.get("models") or []:
        name = str(model.get("name") or "")
        if not name:
            continue
        unit = str(model.get("unit") or model.get("group") or "")
        row = by_name.setdefault(
            name,
            {"name": name, "slug": slug(name), "unit": UNIT_NAMES.get(unit_key(unit), unit), "offers": [], "cheapest_rub": None},
        )
        # цена сравнивается только внутри одной единицы: секунда против минуты не в счёт
        for offer in model.get("offers") or []:
            rub = offer.get("rub")
            offer_unit = str(offer.get("unit") or unit)
            row["offers"].append({"provider": offer.get("provider"), "rub": float(rub) if rub not in (None, "") else None, "unit": offer_unit})
            if rub in (None, "") or unit_key(offer_unit) != unit_key(row["unit"]):
                continue
            if row["cheapest_rub"] is None or float(rub) < row["cheapest_rub"]:
                row["cheapest_rub"] = float(rub)
    # сначала разговорные модели, потом остальные: полки разные, и цена минуты речи
    # рядом с ценой миллиона токенов только путает
    return sorted(
        by_name.values(),
        key=lambda r: (0 if r["unit"] == UNIT_NAMES["token"] else 1, r["cheapest_rub"] is None, r["cheapest_rub"] or 0.0),
    )


def _load_selection(settings: Any) -> dict:
    try:
        data = json.loads(_selection_path(settings).read_text())
    except (OSError, ValueError):
        return {"models": {}, "saved_at": None}
    if not isinstance(data.get("models"), dict):
        data["models"] = {}
    return data


def install(app: Any, settings: Any, admin_dep: Any) -> None:
    """Включает прилавок в релей: чтение рынка и сохранение выбора."""

    @app.get("/v1/admin/menu", dependencies=[Depends(admin_dep)])
    async def admin_menu() -> dict:
        market = _market()
        selection = _load_selection(settings)
        chosen = selection.get("models") or {}
        models = []
        reasons: dict[str, int] = {}
        for row in market:
            found = verdict(row["name"], kind_of(row["unit"]), row["cheapest_rub"])
            saved = chosen.get(row["name"]) or {}
            if not found.keep:
                reasons[found.reason] = reasons.get(found.reason, 0) + 1
            models.append(
                {
                    **row,
                    "shelves": saved.get("shelves") or found.shelves,
                    "junk": "" if found.keep else found.reason,
                    "on": bool(saved["on"]) if "on" in saved else bool(found.keep),
                }
            )
        return {
            "prices_at": _prices_mtime(),
            "saved_at": selection.get("saved_at"),
            "count": {
                "market": len(market),
                "clean": sum(1 for m in models if not m["junk"]),
                "on": sum(1 for m in models if m["on"] and not m["junk"]),
            },
            "junk": reasons,
            "models": models,
        }

    @app.put("/v1/admin/menu", dependencies=[Depends(admin_dep)])
    async def admin_menu_save(body: dict) -> dict:
        incoming = body.get("models")
        if not isinstance(incoming, dict):
            raise ValueError("models must be an object of {name: {on, shelves}}")
        models: dict[str, dict] = {}
        for name, value in incoming.items():
            if not isinstance(value, dict):
                continue
            models[str(name)] = {
                "on": bool(value.get("on")),
                "shelves": [str(s) for s in (value.get("shelves") or []) if str(s).strip()],
            }
        path = _selection_path(settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"saved_at": int(time.time()), "models": models}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
        return {"saved": len(models), "on": sum(1 for m in models.values() if m["on"]), "path": str(path)}


def _prices_mtime() -> str | None:
    try:
        return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(_prices_path().stat().st_mtime))
    except OSError:
        return None
