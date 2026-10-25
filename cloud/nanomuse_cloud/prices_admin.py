"""Цены: одна страница, где видно закупку, розницу и рынок.

Оператор открывает `/app/admin/prices/` и видит три вещи в одном месте:

1. своё меню — по каждой модели закупку в юанях и в рублях, текущую розницу и
   кратность, а рядом предлагаемую цену при заданной наценке;
2. поставщиков — кто подключён сейчас и что говорят снимки их прайсов;
3. розницу рынка — двенадцать сервисов, собранных с их витрин, и черновики
   наших цен из документов (окончательного решения ещё нет).

Маршруты (оба под админским токеном):

    GET /v1/admin/prices  — всё выше одним ответом

Ничего не пишет: страница для решения, а не для продажи. Прайс продажи живёт в
конфигурации релея (`sell_in`/`sell_out` у модели), и меняется она конфигом, а не
этой страницей.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi import Depends

PROVIDERS_DIR = Path(__file__).resolve().parent / "console" / "providers"


def _dir() -> Path:
    """Где лежат данные рынка: по умолчанию рядом с консолью.

    `PRICES_DATA_DIR` нужен тестам (и тому, кто держит снимки отдельно от образа):
    страница читает снимок прайса, розницу и черновики из одного каталога.
    """
    import os

    return Path(os.environ.get("PRICES_DATA_DIR") or PROVIDERS_DIR)


def _read(name: str) -> dict:
    """Один файл из каталога данных: пустой ответ вместо исключения."""
    try:
        return json.loads((_dir() / name).read_text())
    except (OSError, ValueError):
        return {}


def _mtime(name: str) -> str | None:
    try:
        return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime((_dir() / name).stat().st_mtime))
    except OSError:
        return None


def _rate(settings: Any, prices: dict) -> dict:
    """Курс юаня: рабочий, если в релее задан `RUB_PER_USD`, иначе справочный.

    Релей переводит юани в рубли через доллар (`rub_per_usd / usd_cny`). Пока
    `RUB_PER_USD` не задан, этот путь даёт ноль, и себестоимость в рублях
    посчитать нечем: тогда берём курс доллара из снимка цен ЦБ и говорим об этом
    прямо, чтобы цифра на странице не выглядела рабочей.
    """
    usd_cny = float(getattr(settings, "usd_cny", 0) or 0)
    rub_per_usd = float(getattr(settings, "rub_per_usd", 0) or 0)
    fee = float(getattr(settings, "topup_fee_pct", 0) or 0)
    working = rub_per_usd > 0 and usd_cny > 0
    if working:
        return {
            "cny_rub": round(rub_per_usd / usd_cny * (1 + fee / 100), 4),
            "source": "релей",
            "working": True,
            "usd_cny": usd_cny,
            "rub_per_usd": rub_per_usd,
            "topup_fee_pct": fee,
            "at": None,
        }
    snapshot_usd = float(prices.get("usd_rub") or 0)
    if snapshot_usd > 0 and usd_cny > 0:
        return {
            "cny_rub": round(snapshot_usd / usd_cny, 4),
            "source": "снимок ЦБ в prices.json, не рабочий курс релея",
            "working": False,
            "usd_cny": usd_cny,
            "rub_per_usd": 0.0,
            "topup_fee_pct": fee,
            "at": prices.get("generated_at"),
        }
    return {
        "cny_rub": 0.0,
        "source": "курса нет",
        "working": False,
        "usd_cny": usd_cny,
        "rub_per_usd": rub_per_usd,
        "topup_fee_pct": fee,
        "at": None,
    }


def _models(settings: Any, cny_rub: float) -> list[dict]:
    """Строка на каждую модель меню: закупка, розница, кратность."""
    rows: list[dict] = []
    for spec in settings.models:

        def rub(cny: float) -> float | None:
            return round(float(cny) * cny_rub, 4) if cny_rub > 0 and cny else None

        cost_in = rub(getattr(spec, "price_in", 0))
        cost_out = rub(getattr(spec, "price_out", 0))
        sell_in = float(getattr(spec, "sell_in", 0) or 0)
        sell_out = float(getattr(spec, "sell_out", 0) or 0)
        sell_cached = float(getattr(spec, "sell_cached_in", 0) or 0)
        ratio_in = round(sell_in / cost_in, 2) if cost_in and sell_in else None
        ratio_out = round(sell_out / cost_out, 2) if cost_out and sell_out else None
        rows.append(
            {
                "id": spec.id,
                "name": spec.name,
                "kind": spec.kind,
                "lanes": list(spec.lanes),
                "recommended": bool(spec.recommended),
                "listed": bool(spec.listed),
                "default_on": bool(spec.default_on),
                "priced_as": getattr(spec, "priced_as", ""),
                "context": getattr(spec, "context_length", 0),
                "max_output": getattr(spec, "max_output", 0),
                "provider": getattr(spec, "provider", ""),
                "cost": {
                    "cny_in": getattr(spec, "price_in", 0),
                    "cny_out": getattr(spec, "price_out", 0),
                    "cny_in_idle": getattr(spec, "price_in_idle", 0),
                    "cny_out_idle": getattr(spec, "price_out_idle", 0),
                    "rub_in": cost_in,
                    "rub_out": cost_out,
                    "rub_image": rub(getattr(spec, "price_image", 0)),
                    "rub_second": rub(getattr(spec, "price_second", 0)),
                    "rub_speech": rub(getattr(spec, "price_speech", 0)),
                },
                "sell": {
                    "rub_in": sell_in,
                    "rub_out": sell_out,
                    "rub_cached_in": sell_cached,
                    "hint": _sell_hint(spec),
                },
                "ratio": {"in": ratio_in, "out": ratio_out},
            }
        )
    return rows


def _sell_hint(spec: Any) -> str:
    """Чем эта модель продаётся: ставкой в рублях или токенами за штуку."""
    if getattr(spec, "sell_in", 0) or getattr(spec, "sell_out", 0):
        return "ставка ₽ за 1M"
    for field, name in (
        ("per_image", "картинка"),
        ("per_clip", "клип"),
        ("per_speech", "озвучка"),
        ("per_minute", "минута"),
    ):
        if getattr(spec, field, 0):
            return f"{name}: {getattr(spec, field)} токенов"
    return "не задана"


def _providers(settings: Any, prices: dict) -> dict:
    """Кто подключён и что говорят снимки: цена входа по каждому продавцу."""
    connected = sorted((getattr(settings, "providers", {}) or {}).keys())
    snapshot: list[dict] = []
    for entry in prices.get("providers") or []:
        name = str(entry.get("name") or "")
        offers = int(entry.get("offers") or 0)
        snapshot.append(
            {
                "name": name,
                "ok": bool(entry.get("ok")),
                "offers": offers,
                "note": entry.get("note") or "",
                "connected": name in connected,
            }
        )
    # «от 0,12 ₽» и медиана входа: только по строкам «1M токенов (вход)»
    entry_prices: dict[str, list[float]] = {}
    for model in prices.get("models") or []:
        unit = str(model.get("unit") or model.get("group") or "")
        if "вход" in unit:
            for offer in model.get("offers") or []:
                value = offer.get("rub")
                if value in (None, ""):
                    continue
                entry_prices.setdefault(str(offer.get("provider")), []).append(float(value))
    for row in snapshot:
        values = sorted(entry_prices.get(row["name"]) or [])
        if values:
            row["min_in"] = round(values[0], 2)
            row["median_in"] = round(values[len(values) // 2], 2)
            row["models_priced"] = len(values)
    return {
        "connected": connected,
        "snapshot": snapshot,
        "at": prices.get("generated_at"),
        "usd_rub": prices.get("usd_rub"),
        "rate_source": prices.get("rate_source"),
    }


def _measured(value: Any) -> list[dict]:
    """Замеры с живых запросов: в документах это объект «модель → цена»."""
    if isinstance(value, dict):
        rows = []
        for name, price in list(value.items())[:5]:
            if isinstance(price, dict):
                rows.append({"name": name, **price})
            else:
                rows.append({"name": name, "price": price})
        return rows
    if isinstance(value, list):
        return value[:5]
    return []


def _retail() -> dict:
    """Розница рынка: сервисы с их условиями, без простыней по сотням моделей.

    Файл приходит в двух видах: тот, что собирают с витрин (у сервиса есть
    `models` или `ops_rub`), и сжатый, который лежит в репозитории (`priced` и
    `sample` уже посчитаны). Второй — то же самое, только без сотен строк,
    которых страница не показывает.
    """
    data = _read("retail.json")
    services: dict[str, dict] = {}
    for name, entry in (data.get("services") or {}).items():
        if "priced" in entry and "models" not in entry:
            services[name] = {
                "model": entry.get("model") or "",
                "plans": entry.get("plans") or {},
                "ops": (entry.get("ops") or [])[:8],
                "priced": int(entry.get("priced") or 0),
                "sample": (entry.get("sample") or [])[:3],
                "note": entry.get("note") or "",
                "billing": entry.get("billing") or "",
                "measured": _measured(entry.get("measured")),
            }
            continue
        rows = entry.get("models") or entry.get("ops_rub") or []
        services[name] = {
            "model": entry.get("model") or "",
            "plans": entry.get("plans") or {},
            "ops": (entry.get("ops_rub") or [])[:8],
            "priced": len(rows),
            "sample": rows[:3],
            "note": entry.get("note") or entry.get("positioning") or entry.get("models_note") or "",
            "billing": entry.get("billing") or "",
            "measured": _measured(entry.get("measured")),
        }
    return {
        "collected_at": data.get("collected_at"),
        "note": data.get("note") or "",
        "services": services,
    }


def install(app: Any, settings: Any, admin_dep: Any) -> None:
    """Включает страницу цен в релей."""

    @app.get("/v1/admin/prices", dependencies=[Depends(admin_dep)])
    async def admin_prices() -> dict:
        prices = _read("prices.json")
        rate = _rate(settings, prices)
        models = _models(settings, float(rate["cny_rub"] or 0))
        drafts = _read("drafts.json")
        chats = [m for m in models if m["kind"] == "chat"]
        with_sell = [m for m in models if m["sell"]["rub_in"] or m["sell"]["rub_out"]]
        return {
            "rate": rate,
            "generated_at": _mtime("prices.json"),
            "count": {
                "models": len(models),
                "chat": len(chats),
                "media": len(models) - len(chats),
                "with_cost": sum(1 for m in chats if m["cost"]["rub_in"] or m["cost"]["rub_out"]),
                "with_sell": len(with_sell),
            },
            "models": models,
            "providers": _providers(settings, prices),
            "retail": _retail(),
            "drafts": drafts,
        }
