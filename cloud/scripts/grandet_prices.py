#!/usr/bin/env python3
"""Рынок Grandet: официальные цены и самые дешёвые предложения релеев по моделям меню.

Зачем. `provider_prices.py` собирает прайсы наших поставщиков, а Grandet знает ещё
и чужие релеи, и официальные цены вендоров. Оператору нужно видеть, где та же
модель дешевле, чем мы закупаем сейчас.

Что делает. Читает модели меню (`cloud/data/menu-curated.json`), спрашивает по
каждой `GET https://grandet.ai/api/v1/board` и складывает в
`console/providers/grandet.json`:

  * `official`  — цена вендора (USD или CNY за 1M токенов) и её пересчёт в рубли;
  * `inference` — цена у агрегаторов (OpenRouter, Novita и прочие);
  * `market`    — самое дешёвое предложение релея в каждой единице (кредиты
                  Grandet не переводятся в рубли: это отдельная шкала).

Запуск вручную:
    python3 cloud/scripts/grandet_prices.py
    python3 cloud/scripts/grandet_prices.py --out console/providers/grandet.json

Ключ не нужен. Важно: запрос обязан просить gzip (`Accept-Encoding`), иначе
Cloudflare отдаёт большие ответы рывками и соединение зависает.
"""

from __future__ import annotations

import argparse
import gzip
import json
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CLOUD = ROOT / "cloud"
MENU = CLOUD / "data" / "menu-curated.json"
OUT = CLOUD / "nanomuse_cloud" / "console" / "providers" / "grandet.json"

BOARD = "https://grandet.ai/api/v1/board"
UA = "grandet-skill/0.3.0 (nanomuse-cloud)"
CBR = "https://www.cbr-xml-daily.ru/daily_json.js"
FALLBACK = {"USD": 90.0, "CNY": 12.5}

SKILL_VERSION = "0.3.0"
SKILL_BUILD = "grandet-skill-0.3.0-build.20261018b"


def get_json(url: str, timeout: int = 30) -> Any:
    """GET с gzip: без него Cloudflare рвёт длинные ответы."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    return json.loads(body)


def rates() -> tuple[dict[str, float], str]:
    """Курсы ЦБ: рубли за единицу валюты. Недоступен ЦБ — берём запасные."""
    try:
        data = get_json(CBR, timeout=15)
        out = {}
        for code in ("USD", "CNY"):
            row = (data.get("Valute") or {}).get(code) or {}
            nominal = float(row.get("Nominal") or 1) or 1
            out[code] = round(float(row.get("Value") or 0) / nominal, 4)
        if out.get("USD", 0) > 0 and out.get("CNY", 0) > 0:
            return out, "CBR"
    except Exception:  # noqa: BLE001
        pass
    return dict(FALLBACK), "fallback"


def menu_models() -> list[dict[str, Any]]:
    """Модели меню: только те, у кого есть upstream или id для запроса."""
    try:
        data = json.loads(MENU.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = data if isinstance(data, list) else data.get("models") or []
    return [row for row in rows if isinstance(row, dict) and (row.get("upstream") or row.get("id"))]


def board_url(selector: str) -> str:
    query = urllib.parse.urlencode(
        {
            "model": selector,
            "lang": "en",
            "limit": 5,
            "view": "compact",
            "host": "opencode",
            "targetApp": "opencode",
            "skillVersion": SKILL_VERSION,
            "skillBuild": SKILL_BUILD,
        }
    )
    return f"{BOARD}?{query}"


def money(value: Any) -> float | None:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def rub(value: float | None, currency: str, fx: dict[str, float]) -> float | None:
    if value is None:
        return None
    rate = fx.get(currency)
    return round(value * rate, 4) if rate else None


def official_rows(data: dict, fx: dict[str, float]) -> list[dict[str, Any]]:
    out = []
    for row in data.get("official") or []:
        currency = str(row.get("currency") or "").upper()
        inp, outp = money(row.get("input")), money(row.get("output"))
        out.append(
            {
                "vendor": row.get("vendor"),
                "currency": currency,
                "input": inp,
                "output": outp,
                "cache_read": money(row.get("cacheRead")),
                "rub_in": rub(inp, currency, fx),
                "rub_out": rub(outp, currency, fx),
                "observed_at": row.get("observedAt"),
                "conditions": row.get("conditions"),
            }
        )
    return out


def inference_rows(data: dict, fx: dict[str, float]) -> list[dict[str, Any]]:
    out = []
    for row in (data.get("listings") or {}).get("inference") or []:
        currency = str(row.get("currency") or "").upper()
        inp, outp = money(row.get("input")), money(row.get("output"))
        out.append(
            {
                "vendor": row.get("vendor"),
                "currency": currency,
                "input": inp,
                "output": outp,
                "rub_in": rub(inp, currency, fx),
                "rub_out": rub(outp, currency, fx),
                "observed_at": row.get("observedAt"),
            }
        )
    return out


def market_rows(data: dict) -> list[dict[str, Any]]:
    """Самое дешёвое предложение релея в каждой единице: кредиты не пересчитываем."""
    out = []
    for board in data.get("boards") or []:
        cheapest = ((board.get("listings") or {}).get("lowest") or [None])[0]
        if not cheapest:
            continue
        out.append(
            {
                "unit": board.get("unit"),
                "basis": board.get("basis"),
                "provider": cheapest.get("sourceId"),
                "plan": cheapest.get("plan"),
                "blended": cheapest.get("blended"),
                "observed_at": cheapest.get("observedAt"),
                "board_url": cheapest.get("boardUrl"),
            }
        )
    return out


def collect(fx: dict[str, float], limit: int = 0) -> dict[str, Any]:
    models, errors = [], []
    rows = menu_models()
    if limit:
        rows = rows[:limit]
    for row in rows:
        selector = str(row.get("upstream") or row.get("id"))
        item: dict[str, Any] = {
            "key": row.get("id"),
            "name": row.get("name"),
            "selector": selector,
            "kind": row.get("kind") or "chat",
        }
        try:
            data = get_json(board_url(selector))
            if data.get("code") or data.get("error"):
                item["note"] = f"{data.get('code')}: {str(data.get('message'))[:160]}"
                models.append(item)
                continue
            item["official"] = official_rows(data, fx)
            item["inference"] = inference_rows(data, fx)
            item["market"] = market_rows(data)
            item["board_url"] = f"https://grandet.ai/?model={urllib.parse.quote(selector)}"
            models.append(item)
        except Exception as error:  # noqa: BLE001
            errors.append({"key": item["key"], "selector": selector, "error": str(error)[:200]})
        time.sleep(0.2)
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "rates": fx,
        "models": models,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Рынок Grandet по моделям меню")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--limit", type=int, default=0, help="только первые N моделей")
    args = parser.parse_args(argv)

    fx, source = rates()
    payload = collect(fx, args.limit)
    payload["rate_source"] = source
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(args.out)

    ok = len(payload["models"])
    print(f"{args.out}: {ok} моделей, ошибок {len(payload['errors'])}, курс {source}")
    for item in payload["models"][:10]:
        official = (item.get("official") or [{}])[0]
        print(
            f"  {item['key']:<16} {item['selector']:<36} "
            f"official {official.get('currency') or '—'} "
            f"{official.get('input') or '—'}/{official.get('output') or '—'} · "
            f"рынок {len(item.get('market') or [])} ед."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
