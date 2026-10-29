"""Цены: одна страница, где видно закупку, розницу и рынок.

Оператор открывает `/app/admin/prices/` и видит три вещи в одном месте:

1. своё меню — по каждой модели закупку в юанях и в рублях, текущую розницу и
   кратность, а рядом предлагаемую цену при заданной наценке;
2. поставщиков — кто подключён сейчас и что говорят снимки их прайсов;
3. розницу рынка — двенадцать сервисов, собранных с их витрин, и черновики
   наших цен из документов (окончательного решения ещё нет).

Маршруты (оба под админским токеном):

    GET /v1/admin/prices  — всё выше одним ответом

Галочки сохраняют видимость моделей в базе. Закупочные котировки и балансы
обновляются через provider_routing. Прайс продажи остаётся в конфигурации релея;
обновление закупки не меняет обещанные клиенту ставки.
"""

from __future__ import annotations

import json
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException

from . import model_pricing
from .provider_routing import ROUTING_CATALOG_PROVIDERS, identity

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
                "upstream": getattr(spec, "upstream", ""),
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


def _clear_unquoted_purchase_cost(row: dict[str, Any]) -> None:
    """Do not present configuration-era prices as a current supplier quote."""
    row["configured_cost"] = dict(row.get("cost") or {})
    row.setdefault("cost", {})["rub_in"] = None
    row["cost"]["rub_out"] = None
    row["ratio"] = {"in": None, "out": None}
    row["cost_source"] = "no_live_provider_quote"


def _catalog_path() -> Path:
    """Master registry; overrideable for tests and offline catalog builds."""
    import os

    return Path(os.environ.get("MODEL_CATALOG_PATH") or Path(__file__).resolve().parent / "console" / "prices" / "catalog.json")


def _model_kind(source: dict[str, Any]) -> str:
    """Primary task, separate from secondary capabilities such as writing code."""
    mode = str(source.get("mode") or "chat").strip().lower()
    direct = {
        "image_generation": "image",
        "image_edit": "image",
        "video_generation": "video",
        "audio_speech": "audio",
        "audio_transcription": "audio",
        "realtime": "audio",
        "embedding": "embedding",
        "rerank": "rerank",
        "moderation": "safety",
    }
    if mode in direct:
        return direct[mode]
    if mode in {"chat", "completion", "responses", "text_completion"}:
        dev = source.get("dev") if isinstance(source.get("dev"), dict) else {}
        text = " ".join(
            str(value or "")
            for value in (
                source.get("id"),
                source.get("name"),
                source.get("clean_name"),
                dev.get("name"),
            )
        ).lower()
        # Do not label every chat model as a coding model just because it can write code.
        if re.search(r"(^|[\s/_-])(code|coder|coding|codex|codestral|starcoder)([\s/_-]|$)", text):
            return "code"
        return "text"
    return "other"


def _model_keys(value: Any) -> set[str]:
    """Conservative ID forms to compare normalized IDs with provider `/v1/models`."""
    text = str(value or "").strip().lower()
    if not text:
        return set()
    keys = {text}
    if "/" in text:
        keys.add(text.rsplit("/", 1)[-1])
    return keys


def _discovered(settings: Any, router: Any = None) -> dict[str, Any]:
    """Full registry with separate evidence for reference data, quotes and live provider lists.

    A provider name or an old quote is not proof that the current relay key can call a model.
    The live status below means only that a configured provider returned the model from its
    authenticated `/v1/models`; it is not a paid inference probe.
    """
    catalog_path = _catalog_path()
    try:
        data = _catalog_snapshot(str(catalog_path), catalog_path.stat().st_mtime_ns)
    except OSError:
        data = {}
    labels = {
        "text": "Текст",
        "code": "Код",
        "image": "Изображение",
        "video": "Видео",
        "audio": "Аудио",
        "embedding": "Эмбеддинги",
        "rerank": "Ранжирование",
        "safety": "Безопасность",
        "other": "Другое",
    }
    active: set[str] = set()
    for spec in settings.models:
        active.update(_model_keys(getattr(spec, "id", "")))
        active.update(_model_keys(getattr(spec, "upstream", "")))
    configured = {
        str(name)
        for name, provider in (getattr(settings, "providers", {}) or {}).items()
        if callable(getattr(provider, "key", None)) and provider.key()
    }
    live_index: dict[tuple[str, str], bool] = {}
    now = time.time()
    max_age = max(60, int(getattr(router, "max_age", 86400) or 86400))
    for provider, offers in (getattr(router, "offers", {}) or {}).items():
        if str(provider) not in configured:
            continue
        for offer in offers:
            checked_at = float(getattr(offer, "updated", 0) or 0)
            if checked_at <= 0 or now - checked_at > max_age:
                continue
            for value in (getattr(offer, "model", ""), getattr(offer, "canonical_model", "")):
                for key in _model_keys(value):
                    live_index[(str(provider), key)] = True

    rows: list[dict[str, Any]] = []
    for source in data.get("models") or []:
        model_id = str(source.get("id") or "").strip()
        if not model_id:
            continue
        source_keys = set().union(
            *(
                _model_keys(value)
                for value in (
                    model_id,
                    source.get("name"),
                    source.get("ours_name"),
                    source.get("clean_name"),
                )
            )
        )
        dev = source.get("dev") if isinstance(source.get("dev"), dict) else {}
        aa = source.get("aa") if isinstance(source.get("aa"), dict) else {}
        caps = source.get("caps") if isinstance(source.get("caps"), dict) else {}
        offers = source.get("ours") if isinstance(source.get("ours"), dict) else {}
        quote_rows = [
            {"provider": str(provider), "rub": quote.get("rub"), "unit": str(quote.get("unit") or "")}
            for provider, quote in offers.items()
            if isinstance(quote, dict) and quote.get("rub") is not None
        ]
        live_providers = sorted({provider for provider in configured if any(live_index.get((provider, key)) for key in source_keys)})
        kind = _model_kind(source)
        missing = [
            field
            for field, value in (
                ("context", source.get("context")),
                ("max_output", source.get("max_output")),
                ("description", dev.get("description")),
                ("input_price", source.get("world_in_usd")),
                ("output_price", source.get("world_out_usd")),
                ("quality", aa.get("intelligence")),
                ("release_date", dev.get("released")),
                ("modalities", source.get("modalities")),
            )
            if value in (None, "", 0, {}, [])
        ]
        rows.append(
            {
                "id": model_id,
                "name": str(source.get("ours_name") or source.get("clean_name") or source.get("name") or model_id),
                "kind": kind,
                "kind_label": labels[kind],
                "mode": str(source.get("mode") or "chat"),
                "context": source.get("context") or 0,
                "max_output": source.get("max_output") or 0,
                "description": str(dev.get("description") or "").strip(),
                "source_provider": str(dev.get("provider") or source.get("provider") or ""),
                "capabilities": [
                    name
                    for name, enabled in caps.items()
                    if enabled
                    and name
                    in {
                        "code",
                        "image",
                        "audio_in",
                        "audio_out",
                        "video",
                        "tools",
                        "reasoning",
                        "pdf",
                        "open_weights",
                        "caching",
                        "web_search",
                        "structured",
                    }
                ],
                "modalities": source.get("modalities") or {"in": [], "out": []},
                "quality": aa.get("intelligence"),
                "released": dev.get("released"),
                "updated": dev.get("updated"),
                "unit_usd": source.get("unit_usd"),
                "family": source.get("family") or "",
                "offers": quote_rows,
                "live_providers": live_providers,
                "in_menu": bool(source_keys & active),
                "deprecated": bool(source.get("deprecated")),
                "free": bool(source.get("free")),
                "missing": missing,
                "world_in_usd": source.get("world_in_usd"),
                "world_out_usd": source.get("world_out_usd"),
            }
        )
    rows.sort(key=lambda row: (row["kind"], row["name"].casefold(), row["id"].casefold()))
    counts = {kind: sum(row["kind"] == kind for row in rows) for kind in labels}
    return {
        "models": rows,
        "count": len(rows),
        "counts": counts,
        "with_quote_snapshot": sum(bool(row["offers"]) for row in rows),
        "in_live_provider_catalog": sum(bool(row["live_providers"]) for row in rows),
        "in_menu": sum(row["in_menu"] for row in rows),
        "generated_at": data.get("generated_at"),
        "provider_catalog_checked_at": int(
            max(
                [float(getattr(router, "refreshed", 0) or 0)]
                + [
                    float(getattr(offer, "updated", 0) or 0)
                    for offers in (getattr(router, "offers", {}) or {}).values()
                    for offer in offers
                ]
            )
        )
        or None,
    }


@lru_cache(maxsize=1)
def _catalog_snapshot(path: str, modified_ns: int) -> dict[str, Any]:
    """Read the 12 MB market catalogue once; refresh automatically after its daily update."""
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


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
    """Provider IDs from three layers; a source name is not a routing integration."""
    provider_specs = getattr(settings, "providers", {}) or {}
    configured = {str(name) for name, provider in provider_specs.items() if callable(getattr(provider, "key", None)) and provider.key()}
    connected = sorted(configured)
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
                "routing_supported": name in ROUTING_CATALOG_PROVIDERS,
            }
        )
    # «от 0,12 ₽» и медиана входа: только по строкам «1M токенов (вход)»
    entry_prices: dict[str, list[float]] = {}
    excluded_offers: dict[str, int] = {}
    for model in prices.get("models") or []:
        unit = str(model.get("unit") or model.get("group") or "")
        if "вход" in unit:
            for offer in model.get("offers") or []:
                if offer.get("route_eligible") is False:
                    provider_name = str(offer.get("provider") or "")
                    excluded_offers[provider_name] = excluded_offers.get(provider_name, 0) + 1
                    continue
                value = offer.get("rub")
                if value in (None, ""):
                    continue
                entry_prices.setdefault(str(offer.get("provider")), []).append(float(value))
    for row in snapshot:
        row["excluded_offers"] = excluded_offers.get(row["name"], 0)
        values = sorted(entry_prices.get(row["name"]) or [])
        if values:
            row["min_in"] = round(values[0], 2)
            row["median_in"] = round(values[len(values) // 2], 2)
            row["models_priced"] = len(values)
    known_names = (
        "routerai",
        "polza",
        "aitunnel",
        "proxyapi",
        "proxyapi-openai",
        "bothub",
        "neuroapi",
        "openrouter",
        "own",
        "deepseek",
    )
    market_names = {str(row.get("name") or "") for row in snapshot}
    known = [
        {
            "name": name,
            "key_configured": name in configured,
            "routing_supported": name in ROUTING_CATALOG_PROVIDERS,
            "snapshot_present": name in market_names,
        }
        for name in known_names
    ]
    return {
        "connected": connected,
        "known": known,
        "snapshot": snapshot,
        "at": prices.get("generated_at"),
        "usd_rub": prices.get("usd_rub"),
        "rate_source": prices.get("rate_source"),
        "changes": prices.get("changes") or {"new_models": [], "changed_models": [], "new_count": 0, "changed_count": 0},
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


def _slug_keys(*values: Any) -> set[str]:
    """Варианты одного имени модели: `vendor/model`, `model`, с пробелами и точками."""
    out: set[str] = set()
    for value in values:
        if not value:
            continue
        slug = str(value).strip().lower().rsplit("/", 1)[-1]
        for variant in {slug, re.sub(r"\s+", "-", slug), identity(slug)}:
            if variant:
                out.add(variant)
    return out


def _extra_offers(prices: dict, connected: set[str]) -> dict[str, dict[str, dict]]:
    """Предложения поставщиков без адаптера маршрутизации — сейчас это Bothub.

    Релей не умеет отправлять туда запросы, поэтому в `router.routes()` их нет.
    Но прайс у поставщика настоящий (`/api/v2/pricing/models`, уже по нашему
    тарифу), и в таблице его надо видеть рядом с остальными: сравнение
    поставщиков и маржа считаются по нему так же, только без автопереключения.
    """
    wanted = {name for name in connected if name not in ROUTING_CATALOG_PROVIDERS}
    if not wanted:
        return {}
    index: dict[str, dict[str, dict]] = {}
    for row in prices.get("models") or []:
        key = str(row.get("key") or "")
        if not key:
            continue
        for offer in row.get("offers") or []:
            provider = str(offer.get("provider") or "")
            if provider not in wanted:
                continue
            unit = str(row.get("unit") or "")
            field = {
                "1M токенов (вход)": "input_rub",
                "1M токенов (выход)": "output_rub",
                "1M токенов (вход, кэш)": "cached_rub",
            }.get(unit)
            if field is None:
                continue
            try:
                price = float(offer.get("rub"))
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            entry = index.setdefault(key, {}).setdefault(
                provider,
                {
                    "provider": provider,
                    "model": str(offer.get("model") or ""),
                    "context_length": int(row.get("context_length") or 0),
                    "max_output": int(row.get("max_output") or 0),
                    "input_modalities": list(row.get("input_modalities") or []),
                    "output_modalities": list(row.get("output_modalities") or []),
                    "supported_parameters": list(row.get("supported_parameters") or []),
                    "updated": prices.get("generated_at"),
                },
            )
            entry[field] = price
    result: dict[str, dict[str, dict]] = {}
    for key, providers in index.items():
        ready = {}
        for provider, entry in providers.items():
            if entry.get("input_rub") is None or entry.get("output_rub") is None:
                continue
            entry["estimated_rub"] = round((entry["input_rub"] * 1000 + entry["output_rub"] * 512) / 1_000_000, 6)
            entry["eligible"] = False
            entry["selected"] = False
            entry["exclusion_reason"] = "нет адаптера маршрутизации: цена только для сверки"
            ready[provider] = entry
        if ready:
            result[key] = ready
    return result


# Собранный ответ кэшируем: пересчёт маршрутов по 500 моделям стоит около секунды,
# а страница открывается часто. Сайты поставщиков при этом не опрашиваются вообще —
# свежие прайсы приносит только кнопка «Обновить» (refresh=1).
_CACHE: dict[str, Any] = {"at": 0.0, "full": None, "compact": None}
_CACHE_TTL = 300.0

_MODEL_FIELDS = ("id", "name", "kind", "listed", "default_on", "recommended", "context", "max_output", "cost", "sell", "ratio", "priced_as", "upstream")
_OFFER_FIELDS = ("provider", "model", "endpoint", "input_rub", "output_rub", "cached_rub", "context_length", "eligible", "selected", "exclusion_reason")


def _compact(payload: dict) -> dict:
    """Только то, что рисует админка: без модальностей, параметров и рынка."""
    models = []
    for row in payload.get("models") or []:
        item = {key: row.get(key) for key in _MODEL_FIELDS}
        routing = row.get("routing")
        if routing:
            item["routing"] = {
                "selected": routing.get("selected"),
                "selected_model": routing.get("selected_model"),
                "offers": [
                    {key: offer.get(key) for key in _OFFER_FIELDS}
                    for offer in routing.get("offers") or []
                ],
            }
        models.append(item)
    return {
        "rate": payload.get("rate"),
        "generated_at": payload.get("generated_at"),
        "count": payload.get("count"),
        "plan_discounts": payload.get("plan_discounts"),
        "providers": payload.get("providers"),
        "models": models,
    }


# Поля прайса, которые можно пересчитать пачкой: ключ API → поле в rates_rub.
_BULK_RATE_FIELDS = ("input", "output", "cached_input", "image", "image_2k", "image_input", "second", "speech", "minute")


def _bulk_rates(item: dict, multiplier: float) -> dict:
    rates = {}
    for key in _BULK_RATE_FIELDS:
        value = item.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number <= 0:
            continue
        rates[key] = round(number * multiplier, 6)
    return rates


def install(app: Any, settings: Any, admin_dep: Any, *, cloud: Any = None, router: Any = None) -> None:
    """Включает страницу цен в релей."""

    @app.get("/v1/admin/prices", dependencies=[Depends(admin_dep)])
    async def admin_prices(refresh: int = 0, compact: int = 0) -> dict:
        now = time.time()
        if not refresh and _CACHE["full"] is not None and now - _CACHE["at"] <= _CACHE_TTL:
            return _CACHE["compact"] if compact else _CACHE["full"]
        prices = _read("prices.json")
        rate = _rate(settings, prices)
        models = _models(settings, float(rate["cny_rub"] or 0))
        plan_discounts = [
            {"id": row["id"], "name": row["name"], "discount_pct": row["discount_pct"]}
            for row in (cloud.plan_menu() if cloud is not None and callable(getattr(cloud, "plan_menu", None)) else [])
        ]
        max_discount_pct = max((int(row["discount_pct"]) for row in plan_discounts), default=0)
        configured = {
            str(name)
            for name, provider in (getattr(settings, "providers", {}) or {}).items()
            if callable(getattr(provider, "key", None)) and provider.key()
        }
        extra = _extra_offers(prices, configured)
        if router is not None:
            for row, spec in zip(models, settings.models, strict=True):
                if cloud is not None:
                    row["listed"] = cloud.model_visible(spec)
                if spec.kind != "chat":
                    continue
                body = {"model": spec.upstream, "messages": [{"role": "user", "content": "a" * 3000}], "max_tokens": 512}
                routes = router.routes(spec, body, "", {})
                selected_route = routes[0] if routes else None
                all_quotes = router.routes(spec, body, "", {}, include_all=True)
                info_route = selected_route or next(
                    (candidate for candidate in all_quotes if candidate.offer and candidate.routing_eligible), None
                )
                if info_route and info_route.offer:
                    row["context"] = row["context"] or info_route.offer.context
                    row["max_output"] = row["max_output"] or info_route.offer.output_limit
                    row["input_modalities"] = list(info_route.offer.modalities)
                    row["output_modalities"] = list(info_route.offer.output_modalities)
                    row["supported_parameters"] = list(info_route.offer.parameters)
                offers = []
                for route in all_quotes:
                    if route.offer is not None:
                        offer = route.offer
                        offers.append(
                            {
                                "provider": route.provider,
                                "model": offer.model,
                                "input_rub": offer.input_rub,
                                "output_rub": offer.output_rub,
                                "cached_rub": offer.cached_rub,
                                "updated": offer.updated,
                                "endpoint": offer.endpoint,
                                "estimated_rub": offer.estimate(1000, 512),
                                "context_length": offer.context,
                                "max_output": offer.output_limit,
                                "input_modalities": list(offer.modalities),
                                "output_modalities": list(offer.output_modalities),
                                "supported_parameters": list(offer.parameters),
                                "eligible": route.routing_eligible
                                and any(r.provider == route.provider and r.body.get("model") == route.body.get("model") for r in routes),
                                "exclusion_reason": route.exclusion_reason
                                or (
                                    "за пределами диапазона переключения"
                                    if route.routing_eligible
                                    and not any(
                                        r.provider == route.provider and r.body.get("model") == route.body.get("model") for r in routes
                                    )
                                    else ""
                                ),
                                "selected": bool(
                                    selected_route
                                    and route.provider == selected_route.provider
                                    and route.body.get("model") == selected_route.body.get("model")
                                ),
                            }
                        )
                # Bothub и подобные: цена есть, адаптера маршрутизации нет.
                # Матчим по имени модели у всех, кто уже дал котировку, и по upstream.
                for key in _slug_keys(spec.upstream, spec.id, *(item["model"] for item in offers)):
                    for provider, snapshot_offer in (extra.get(key) or {}).items():
                        if any(item["provider"] == provider for item in offers):
                            continue
                        offers.append(dict(snapshot_offer))
                row["routing"] = {
                    "offers": offers,
                    "selected": selected_route.provider if selected_route and selected_route.offer else None,
                    "selected_model": selected_route.body.get("model") if selected_route and selected_route.offer else None,
                    "configured": {"provider": spec.provider, "model": spec.upstream},
                    "basis": "1000 input + 512 output, no cache, no tools",
                }
                if selected_route and selected_route.offer:
                    row["cost"]["rub_in"], row["cost"]["rub_out"] = selected_route.offer.rates()[:2]
                    selected = selected_route.offer
                    row["ratio"] = {
                        "in": row["sell"]["rub_in"] / selected.rates()[0] if selected.rates()[0] else None,
                        "out": row["sell"]["rub_out"] / selected.rates()[1] if selected.rates()[1] else None,
                    }
                    row["cost_source"] = "live_provider_quote"
                else:
                    _clear_unquoted_purchase_cost(row)
        actuals = {}
        if cloud is not None:
            with cloud.db._lock:
                entries = cloud.db._conn.execute(
                    "SELECT model,ts,prompt_tokens,completion_tokens,extra FROM ledger "
                    "WHERE kind='chat' AND ts>=? ORDER BY id DESC LIMIT 3000",
                    (int(time.time()) - 7 * 86400,),
                ).fetchall()
            for entry in entries:
                try:
                    meta = json.loads(entry["extra"] or "{}").get("model_accounting", {})
                except (ValueError, AttributeError):
                    continue
                if not meta.get("provider") or entry["model"] in actuals:
                    continue
                actuals[entry["model"]] = {
                    "provider": meta["provider"],
                    "at": entry["ts"],
                    "cost_rub": meta.get("provider_cost_rub"),
                    "basis": meta.get("provider_cost_basis"),
                    "input_tokens": entry["prompt_tokens"],
                    "output_tokens": entry["completion_tokens"],
                }
        for row in models:
            row["last_request"] = actuals.get(row["id"])
        if cloud is not None:
            for row in models:
                spec = settings.model(row["id"])
                commerce = model_pricing.describe(cloud, spec)
                row["commerce"] = commerce
                rates = commerce["rates_rub"]
                row["sell"].update(rub_in=rates["input"], rub_out=rates["output"], rub_cached_in=rates["cached_input"])
                # Наценка считается от РОЗНИЦЫ, а розница только что сменилась: без
                # этого пересчёта после массовой правки колонка показывала старые числа.
                if spec.kind == "chat":
                    cost_in, cost_out = row["cost"].get("rub_in"), row["cost"].get("rub_out")
                    row["ratio"] = {
                        "in": round(rates["input"] / cost_in, 6) if cost_in else None,
                        "out": round(rates["output"] / cost_out, 6) if cost_out else None,
                    }
                row["below_cost"] = (
                    any(
                        row["cost"].get("rub_" + side) and rates[field] * (100 - max_discount_pct) / 100 < row["cost"]["rub_" + side]
                        for side, field in (("in", "input"), ("out", "output"))
                    )
                    if spec.kind == "chat"
                    else commerce["source"] == "legacy_cost"
                )
        drafts = _read("drafts.json")
        chats = [m for m in models if m["kind"] == "chat"]
        with_sell = [m for m in models if m["sell"]["rub_in"] or m["sell"]["rub_out"]]
        payload = {
            "routing": router.status() if router is not None else None,
            "plan_discounts": plan_discounts,
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
            "discovery": _discovered(settings, router),
            "providers": _providers(settings, prices),
            "retail": _retail(),
            "drafts": drafts,
        }
        _CACHE["full"] = payload
        _CACHE["compact"] = _compact(payload)
        _CACHE["at"] = now
        return _CACHE["compact"] if compact else payload

    @app.post("/v1/admin/prices/bulk/pricing", dependencies=[Depends(admin_dep)])
    async def bulk_pricing(body: dict) -> dict:
        """Пачка: цена клиенту = базовая ставка × множитель.

        Клиент присылает то, что видит в таблице (закупку выбранного маршрута),
        сервер только умножает и пишет перекрытия model_retail_prices. Так массовая
        правка не зависит от того, какие маршруты выбраны в момент нажатия.
        """
        if cloud is None:
            raise HTTPException(503, "Model pricing storage is unavailable")
        try:
            multiplier = float(body.get("multiplier"))
        except (TypeError, ValueError):
            raise HTTPException(422, "multiplier must be a number")
        if not 0.1 <= multiplier <= 20:
            raise HTTPException(422, "multiplier must be between 0.1 and 20")
        items = body.get("items")
        if not isinstance(items, list) or not items:
            raise HTTPException(422, "items is empty")
        dry_run = bool(body.get("dry_run"))
        updated, failed, preview = 0, [], []
        for item in items[:2000]:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "")
            spec = settings.model(model_id) if model_id else None
            if spec is None:
                failed.append({"id": model_id, "error": "unknown model"})
                continue
            rates = _bulk_rates(item, multiplier)
            if not rates:
                failed.append({"id": model_id, "error": "no base rates"})
                continue
            if dry_run:
                preview.append({"id": model_id, "rates_rub": rates})
                updated += 1
                continue
            revision = model_pricing.policy(cloud, spec)["revision"]
            try:
                model_pricing.save(cloud, spec, {"revision": revision, "rates_rub": rates})
                updated += 1
            except Exception as error:  # noqa: BLE001
                failed.append({"id": model_id, "error": str(error)[:160]})
        if not dry_run:
            _CACHE["at"] = 0.0
        return {"updated": updated, "total": len(items), "failed": failed[:20], "preview": preview[:10]}

    @app.post("/v1/admin/prices/bulk/visibility", dependencies=[Depends(admin_dep)])
    async def bulk_visibility(body: dict) -> dict:
        """Показать или скрыть пачку моделей: одна запись в аудит на всю пачку."""
        if cloud is None:
            raise HTTPException(503, "Model visibility storage is unavailable")
        visible = body.get("visible")
        if not isinstance(visible, bool):
            raise HTTPException(422, "visible must be a boolean")
        ids = body.get("ids")
        if not isinstance(ids, list) or not ids:
            raise HTTPException(422, "ids is empty")
        updated, failed = 0, []
        for model_id in ids[:2000]:
            spec = settings.model(str(model_id)) if model_id else None
            if spec is None:
                failed.append(str(model_id))
                continue
            if visible and not spec.available:
                failed.append(str(model_id))
                continue
            cloud.set_model_visibility(spec.id, visible)
            updated += 1
        cloud.note("", "model.visibility.bulk", f"{updated} моделей: {'показать' if visible else 'скрыть'}")
        _CACHE["at"] = 0.0
        return {"updated": updated, "total": len(ids), "failed": failed[:20]}

    @app.put("/v1/admin/prices/models/{model_id}/pricing", dependencies=[Depends(admin_dep)])
    async def save_model_price(model_id: str, body: dict) -> dict:
        spec = settings.model(model_id)
        if cloud is None or spec is None:
            raise HTTPException(404, "Unknown model")
        return model_pricing.save(cloud, spec, body)

    @app.put("/v1/admin/prices/models/{model_id}/visibility", dependencies=[Depends(admin_dep)])
    async def model_visibility(model_id: str, body: dict) -> dict:
        if cloud is None:
            raise HTTPException(503, "Model visibility storage is unavailable")
        spec = settings.model(model_id)
        if spec is None:
            raise HTTPException(404, "Unknown model")
        if not isinstance(body.get("visible"), bool):
            raise HTTPException(422, "visible must be a boolean")
        if body["visible"] and not spec.available:
            raise HTTPException(409, "This model is unavailable")
        cloud.set_model_visibility(spec.id, body["visible"])
        cloud.note("", "model.visibility", f"{spec.id}: {body['visible']}")
        return {"id": spec.id, "visible": cloud.model_visible(spec)}
