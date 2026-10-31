#!/usr/bin/env python3
"""Цены ПОСТАВЩИКОВ: одна таблица на всех, чтобы видеть, у кого дешевле.

Модуль ничего не решает и никого не переключает — он только собирает прайсы
в один файл и говорит, у кого на каждую модель дешевле. Поставщиков можно
добавлять: достаточно новой функции `fetch_<name>()`, возвращающей предложения
`{model, unit, price, currency}`.

Запуск вручную:
    python -m nanomuse_cloud.provider_prices --out console/providers/prices.json
На сервере этим занимается таймер (systemd), страница админки читает готовый
JSON и показывает кнопку «Обновить данные».

Источники по умолчанию:
  * polza.ai      — каталог в рублях, цена карточки модели (за картинку/видео/1M);
  * routerai.ru   — рублёвый агрегатор, цена входа и выхода за 1M токенов;
  * bothub.chat   — цены API-ключа (`/api/v2/pricing/models`) в рублях, уже по нашему тарифу;
  * api-docs.deepseek.com — официальный прайс DeepSeek в долларах.
"""

from __future__ import annotations

import concurrent.futures as cf
import gzip
import html
import json
import os
import re
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
TIMEOUT = 25
CBR_URL = "https://www.cbr-xml-daily.ru/daily_json.js"
FALLBACK_USD_RUB = 90.0

PRICE = re.compile(r"(\d[\d\u00a0 ]*(?:[.,]\d+)?)\s*(₽|руб\.?|\$|USD)", re.I)
UNIT_WORDS = [
    (r"за\s*1\s*[МM]\s*токен|per\s*1M\s*token", "1M токенов"),
    (r"за\s*1\s*[МM]\s*символ|per\s*1M\s*char", "1M символов"),
    (r"изображен|image", "изображение"),
    (r"видео|video", "видео"),
    (r"секунд|second", "секунда"),
    (r"минут|minute", "минута"),
    (r"запрос|request", "запрос"),
]


# ── сеть ────────────────────────────────────────────────────────────────
def _ctx():
    import ssl

    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        try:
            return ssl.create_default_context()
        except Exception:  # noqa: BLE001
            return None


def get(url: str, timeout: int = TIMEOUT) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept-Language": "ru,en;q=0.8",
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        },
    )
    kwargs = {"timeout": timeout}
    ctx = _ctx()
    if ctx is not None:
        kwargs["context"] = ctx
    with urllib.request.urlopen(req, **kwargs) as r:
        raw = r.read()
    for enc in ("utf-8", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def text_of(body: str) -> str:
    body = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", body)
    body = re.sub(r"(?s)<[^>]+>", "\n", body)
    body = html.unescape(body)
    return "\n".join(ln.strip() for ln in body.splitlines() if ln.strip())


def number(raw: str) -> float | None:
    s = raw.replace("\u00a0", "").replace(" ", "").replace(",", ".")
    if s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def unit_near(line: str, following: str) -> str:
    hay = f"{line} {following}"
    low = hay.lower()
    for pat, name in UNIT_WORDS:
        if re.search(pat, low):
            return name
    return "?"


# ── курс доллара ────────────────────────────────────────────────────────
def usd_rub() -> tuple[float, str]:
    try:
        data = json.loads(get(CBR_URL, 15))
        val = float(data["Valute"]["USD"]["Value"])
        if 20 < val < 500:
            return round(val, 2), "cbr"
    except Exception:  # noqa: BLE001
        pass
    return FALLBACK_USD_RUB, "fallback"


# ── поставщики ──────────────────────────────────────────────────────────
JIUGX_PRICING = "https://jiugx.com/api/pricing"


def jiugx_offers(data: dict) -> list[dict]:
    """Ставки jiugx.com из `/api/pricing` (New API), в долларах.

    `quota_type=0` — за токены: $/1M входа = model_ratio × 2 × group_ratio,
    выход — та же ставка × completion_ratio. `quota_type=1` — фиксированная
    цена за вызов: model_price × group_ratio.

    Формулу сверили живым запросом `deepseek-v4-1-flash-260910` (33 входа +
    8 выхода): списание $0,000146 совпало с расчётом по этим полям.
    """
    try:
        ratio = float((data.get("group_ratio") or {}).get("default") or 1)
    except (TypeError, ValueError):
        ratio = 1.0
    offers: list[dict] = []
    for row in data.get("data") or []:
        name = str(row.get("model_name") or "").strip()
        if not name:
            continue
        try:
            quota_type = int(row.get("quota_type") or 0)
            model_ratio = float(row.get("model_ratio") or 0)
            completion = float(row.get("completion_ratio") or 0)
            fixed = float(row.get("model_price") or 0)
        except (TypeError, ValueError):
            continue
        if quota_type == 0:
            price_in = model_ratio * 2 * ratio
            if price_in <= 0:
                continue
            offers.append(dict(model=name, unit="1M токенов (вход)", price=round(price_in, 6), currency="USD"))
            if completion > 0:
                offers.append(
                    dict(model=name, unit="1M токенов (выход)", price=round(price_in * completion, 6), currency="USD")
                )
            continue
        price = fixed * ratio
        if price <= 0:
            continue
        tags = str(row.get("tags") or "").lower()
        low = name.lower()
        if "tts" in tags or "audio" in tags:
            unit = "запрос"
        elif "video" in tags or any(word in low for word in ("video", "seedance")):
            unit = "видео"
        elif "image" in tags or any(word in low for word in ("image", "seedream")):
            unit = "изображение"
        else:
            unit = "запрос"
        offers.append(dict(model=name, unit=unit, price=round(price, 6), currency="USD"))
    return offers


def fetch_jiugx() -> list[dict]:
    """jiugx.com — китайский релей. Прайс открыт, ключ для сбора не нужен."""
    return jiugx_offers(json.loads(get(JIUGX_PRICING, 30)))


def fetch_deepseek() -> list[dict]:
    """Официальный прайс DeepSeek: $ за 1M токенов, peak и off-peak."""
    body = get("https://api-docs.deepseek.com/quick_start/pricing/")
    lines = text_of(body).splitlines()
    start = next((i for i, ln in enumerate(lines) if "MODEL" == ln), 0)
    models, i = [], start
    while i < len(lines) and len(models) < 4:
        if re.fullmatch(r"deepseek[\w.\-]*", lines[i]):
            models.append(lines[i])
            i += 1
            continue
        if lines[i].startswith("BASE URL"):
            break
        i += 1
    if not models:
        models = ["deepseek-flash", "deepseek-v4-pro"]
    offers: list[dict] = []

    # второй, надёжный проход: собираем группы по (раздел, пик/не пик)
    groups: dict[tuple[str, str], list[float]] = {}
    section, peak = None, False
    for ln in lines:
        low = ln.lower()
        if "cache hit" in low:
            section = "вход (кэш)"
            continue
        if "cache miss" in low:
            section = "вход"
            continue
        if "1m output" in low:
            section = "выход"
            continue
        if ln.strip() == "PEAK":
            peak = True
            continue
        if ln.strip() == "OFF-PEAK":
            peak = False
            continue
        m = re.fullmatch(r"\$(\d+(?:\.\d+)?)", ln.strip())
        if m and section:
            groups.setdefault((section, "peak" if peak else "off-peak"), []).append(float(m.group(1)))
    for (sec, mode), vals in groups.items():
        for i, val in enumerate(vals):
            if i >= len(models):
                break
            kind = {"вход (кэш)": "вход, кэш", "вход": "вход", "выход": "выход"}.get(sec, sec)
            # базой считаем вне-пиковые цены (обычный тариф, как у перепродавцов);
            # пиковые идут отдельными строками, чтобы их не путать с базой
            suffix = ", пик" if mode == "peak" else ""
            offers.append(dict(model=models[i], unit=f"1M токенов ({kind}{suffix})", price=val, currency="USD", meta=mode))
    return offers


def _auth_get(url: str, key: str, timeout: int = 30) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "User-Agent": UA,
            "Accept": "application/json",
        },
    )
    kwargs = {"timeout": timeout}
    ctx = _ctx()
    if ctx is not None:
        kwargs["context"] = ctx
    with urllib.request.urlopen(req, **kwargs) as r:
        return r.read().decode("utf-8", "replace")


def fetch_routerai() -> list[dict]:
    """routerai.ru — рубли. С ключом берём открытый каталог /api/v1/models
    (574 модели, цены за токен); без ключа разбираем страницу каталога."""
    key = os.environ.get("PRICE_ROUTERAI_KEY", "").strip()
    if key:
        data = json.loads(_auth_get("https://routerai.ru/api/v1/models", key))
        offers = []
        units = {
            "prompt": "1M токенов (вход)",
            "completion": "1M токенов (выход)",
            "input_cache_read": "1M токенов (вход, кэш)",
            "input_cache_write": "1M токенов (кэш-запись)",
            "image_output": "1M токенов (картинка)",
            "audio_output": "1M токенов (звук)",
            "internal_reasoning": "1M токенов (размышление)",
        }
        for row in data.get("data", []):
            pricing = row.get("pricing") or {}
            per_units = row.get("pricing_units") or {}
            for field, unit in units.items():
                val = pricing.get(field)
                if not isinstance(val, (int, float)) or val <= 0:
                    continue
                if per_units.get(field) != "token":  # за запрос и т.п. — отдельно
                    continue
                offers.append(dict(model=row.get("id", "?"), unit=unit, price=round(val * 1_000_000, 4), currency="RUB"))
            req_price = pricing.get("web_search")  # цена за ОДИН запрос, не за миллион
            if isinstance(req_price, (int, float)) and req_price > 0:
                offers.append(dict(model=row.get("id", "?"), unit="запрос", price=round(req_price, 4), currency="RUB"))
        return offers
    # без ключа — страница каталога
    body = get("https://routerai.ru/models")
    lines = text_of(body).splitlines()
    offers, model = [], None
    slug_re = re.compile(r"^[a-z0-9][a-z0-9\-]*/[a-z0-9][a-z0-9.\-]*$")
    for i, ln in enumerate(lines):
        if slug_re.match(ln.strip()):
            model = ln.strip()
            continue
        if not re.search(r"токены за 1M", ln, re.I):
            continue
        m = PRICE.search(" ".join(lines[i : i + 2]))
        if not m:
            continue
        price = number(m.group(1))
        if price is None or price <= 0:
            continue
        kind = "вход" if "Входящ" in ln else "выход" if "Исходящ" in ln else "?"
        offers.append(dict(model=model or "routerai", unit=f"1M токенов ({kind})", price=price, currency="RUB"))
    return offers


def fetch_polza(limit: int = 0, workers: int = 12) -> list[dict]:
    """polza.ai — рубли. Каталог `/api/v1/models` отдаёт ставки по единицам:

      prompt_per_million / completion_per_million — текст за 1M токенов;
      image_output_per_million                   — картинка за 1M токенов;
      tts_per_million_characters                 — озвучка за 1M знаков;
      stt_per_minute                             — расшифровка за минуту;
      per_request / tiers[].cost_rub             — картинка, видео, музыка за штуку.

    Страницы моделей больше не разбираем: на них одна и та же цена встречается
    в разных подписях, и в прайс попадали чужие единицы (например, цена за 1M
    токенов картинки выдавалась за цену одной картинки). Здесь единица известна
    из поля, а не угадывается регуляркой.

    `limit`/`workers` оставлены для совместимости вызова: теперь это один
    запрос каталога, а не обход страниц.
    """
    data = json.loads(get("https://polza.ai/api/v1/models", 60))
    rows = data.get("data") or []
    if limit:
        rows = rows[:limit]

    # Как поставщик называет единицу в зависимости от типа модели. Видео с
    # `unitParam=duration` — цена за секунду; без него в `tiers` лежит цена за
    # готовый клип (у Kling 5 секунд стоят 42 ₽, а не 42 ₽/с).
    tier_unit = {"image": "изображение", "music": "запрос"}
    request_unit = {"image": "изображение", "video": "видео", "music": "запрос"}
    units = (
        ("prompt_per_million", "1M токенов (вход)"),
        ("completion_per_million", "1M токенов (выход)"),
        ("input_cache_read_per_million", "1M токенов (вход, кэш)"),
        ("image_output_per_million", "1M токенов (картинка)"),
        ("tts_per_million_characters", "1M символов"),
        ("stt_per_minute", "минута"),
        ("video_per_second", "секунда"),
    )

    offers: list[dict] = []
    for model in rows:
        slug = str(model.get("id") or "")
        if not slug:
            continue
        pricing = (model.get("top_provider") or {}).get("pricing") or {}
        if str(pricing.get("currency") or "RUB").upper() != "RUB":
            continue
        kind = str(model.get("type") or "")

        def add(unit: str, raw: Any, model_slug: str = slug) -> None:
            try:
                price = float(str(raw).replace(",", "."))
            except (TypeError, ValueError):
                return
            if price > 0:
                offers.append(dict(model=model_slug, unit=unit, price=round(price, 4), currency="RUB"))

        for field, unit in units:
            add(unit, pricing.get(field))
        tiers = pricing.get("tiers")
        if isinstance(tiers, list):
            prices: list[float] = []
            for tier in tiers:
                if not isinstance(tier, dict):
                    continue
                try:
                    price = float(str(tier.get("cost_rub")).replace(",", "."))
                except (TypeError, ValueError):
                    continue
                if price > 0:
                    prices.append(price)
            if prices:
                # На карточке несколько тарифов (разрешение/качество). Для
                # сравнения берём самый дешёвый — как и раньше на странице.
                if kind == "video":
                    unit = "секунда" if pricing.get("unitParam") == "duration" else "видео"
                else:
                    unit = tier_unit.get(kind, "запрос")
                add(unit, min(prices))
        add(request_unit.get(kind, "запрос"), pricing.get("per_request"))
    return offers


def _money(line: str) -> float | None:
    m = PRICE.search(line)
    if not m:
        return None
    val = number(m.group(1))
    if val is None or val <= 0:
        return None
    return val


def fetch_proxyapi() -> list[dict]:
    """proxyapi.ru — рубли. В карточке модели подпись и цена на разных строках:
    «Ввод | 632 ₽ | за 1М токенов», «Видео 720p без звука | 15,79 ₽ | за 1 секунду».

    Карточка заканчивается следующим «вендор / slug», поэтому модель запоминаем
    по разделителю, а единицу читаем из подписи цены, а не угадываем.
    """
    lines = text_of(get("https://proxyapi.ru/pricing")).splitlines()
    offers, model = [], None
    for i, ln in enumerate(lines):
        text = ln.strip()
        # карточка: «Название | вендор | / | slug | от | Вендор»
        if text == "/" and i >= 2 and i + 1 < len(lines):
            model = f"{lines[i - 1]}/{lines[i + 1]}"
            continue
        if not model:
            continue
        low = text.lower()
        if low.startswith(("ввод", "вывод", "изображени")):
            tail = " ".join(lines[i : i + 3])
            price = _money(tail)
            if price is None or ("токен" not in tail.lower() and "изображени" not in low):
                continue
            unit = "1M токенов (вход)" if low.startswith("ввод") else "1M токенов (выход)" if low.startswith("вывод") else "изображение"
            offers.append(dict(model=model, unit=unit, price=price, currency="RUB"))
            continue
        # медиа: подпись «Видео 720p без звука», цена и единица — следующие строки
        if not low.startswith(("видео", "изображение", "аудио")):
            continue
        tail = " ".join(lines[i : i + 3]).lower()
        price = _money(tail)
        if price is None:
            continue
        if "за 1 секунду" in tail:
            unit = "секунда"
        elif "за 1 минуту" in tail:
            unit = "минута"
        elif "за 1 изображение" in tail:
            unit = "изображение"
        elif "за 1М символов" in tail or "за 1м символов" in tail:
            unit = "1M символов"
        else:
            continue
        offers.append(dict(model=model, unit=unit, price=price, currency="RUB"))
    # на странице есть дубли (карточка + сводка) и разные тарифы — берём самый дешёвый
    best: dict[tuple[str, str], dict] = {}
    for off in offers:
        key = (off["model"], off["unit"])
        if key not in best or off["price"] < best[key]["price"]:
            best[key] = off
    return list(best.values())


BOTHUB_API = "https://bothub.chat/api/v2/pricing/models"

# Единицы Bothub → единицы этой таблицы. Ключ берём вместе с единицей: у картинок
# и видео key совпадает с unit, у текста key говорит, вход это или выход.
BOTHUB_UNITS = {
    ("per_1m_tokens", "input"): "1M токенов (вход)",
    ("per_1m_tokens", "cache_read"): "1M токенов (вход, кэш)",
    ("per_1m_tokens", "cache_write"): "1M токенов (кэш-запись)",
    ("per_1m_tokens", "cache_write_1h"): "1M токенов (кэш-запись)",
    ("per_1m_tokens", "output"): "1M токенов (выход)",
    ("per_1m_tokens", "input_image"): "1M токенов (вход, картинка)",
    ("per_1m_tokens", "output_image"): "1M токенов (выход, картинка)",
    ("per_1k_tokens", "input"): "1000 токенов (вход)",
    ("per_1k_tokens", "cache_read"): "1000 токенов (вход, кэш)",
    ("per_1k_tokens", "output"): "1000 токенов (выход)",
    ("per_1k_chars", None): "1000 символов",
    ("per_image", None): "изображение",
    ("per_second", None): "секунда",
    ("per_minute", None): "минута",
}


def fetch_bothub() -> list[dict]:
    """bothub.chat — рубли по тарифу нашего API-ключа.

    Страница bothub.chat/models показывает акцию и не совпадает с тем, что
    списывают с ключа. Авторитетный источник — `/api/v2/pricing/models`:
    в поле `rub` уже цена для нашего тарифа (PREMIUM, 200 ₽ за 1M Caps).
    Без ключа остаётся разбор страницы.
    """
    key = os.environ.get("BOTHUB_KEY", "").strip()
    if not key:
        return _fetch_bothub_page()
    data = json.loads(_auth_get(BOTHUB_API, key))
    offers = []
    for model in data.get("models") or []:
        name = model.get("label") or model.get("id")
        if not name:
            continue
        features = model.get("features") or []
        generation_unit = (
            "изображение" if "TEXT_TO_IMAGE" in features else "видео" if "TEXT_TO_VIDEO" in features else ""
        )
        generation = [
            float(price["rub"])
            for price in model.get("generationPrices") or []
            if isinstance(price.get("rub"), (int, float)) and float(price["rub"]) > 0
        ]
        if generation_unit and generation:
            # `generationPrices` — это реальная цена за готовую картинку/клип
            # (1K/2K/4K, размеры). `per_image` у Bothub бывает базовой ставкой
            # вроде 0,0002 ₽ и к реальному счёту отношения не имеет.
            offers.append(
                dict(model=str(name), unit=generation_unit, price=round(min(generation), 4), currency="RUB")
            )
        for price in model.get("prices") or []:
            unit = BOTHUB_UNITS.get((price.get("unit"), price.get("key")))
            if unit is None:
                unit = BOTHUB_UNITS.get((price.get("unit"), None))
            if unit is None:
                continue
            if generation_unit and generation and unit == generation_unit:
                continue
            rub = price.get("rub")
            if not isinstance(rub, (int, float)) or rub <= 0:
                continue
            offers.append(dict(model=str(name), unit=unit, price=round(float(rub), 4), currency="RUB"))
    return offers


def _fetch_bothub_page() -> list[dict]:
    """Запасной путь без ключа: рубли из карточки «Ввод (за 1M токенов) 156,46 ₽»,
    название модели стоит сразу после блока цен."""
    lines = text_of(get("https://bothub.chat/models")).splitlines()
    offers, pending = [], []
    label_re = re.compile(r"^(Ввод|Вывод|Картинка-промпт)\s*\(([^)]{1,24})\)$", re.I)

    def unit_of(label: str, paren: str) -> str:
        paren = paren.lower()
        if "токен" in paren:
            return "1M токенов (вход)" if label.lower().startswith("ввод") else "1M токенов (выход)"
        if "изображ" in paren or "1k" in paren or "картин" in paren:
            return "изображение"
        return paren

    current = None
    for i, ln in enumerate(lines):
        m = label_re.match(ln.strip())
        if m:
            price = _money(" ".join(lines[i : i + 3]))
            if price is not None:
                pending.append((unit_of(m.group(1), m.group(2)), price))
            continue
        if pending:
            # название модели: первая «осмысленная» строка после цен
            cand = ln.strip()
            if 2 < len(cand) < 70 and not PRICE.search(cand) and not cand.startswith(("(", "New", "-", "Контекст")):
                current = cand
                for unit, price in pending:
                    offers.append(dict(model=current, unit=unit, price=price, currency="RUB"))
                pending = []
    return offers


def fetch_neuroapi() -> list[dict]:
    """neuroapi.host — рубли за 1M токенов; у модели обычно два тарифа."""
    lines = text_of(get("https://neuroapi.host/pricing")).splitlines()
    offers = []
    for i, ln in enumerate(lines):
        if not re.match(r"^за\s*1\s*млн\s*токенов$", ln.strip(), re.I) or i == 0:
            continue
        model = lines[i - 1].strip()
        if not (2 < len(model) < 60) or PRICE.search(model):
            continue
        # разбираем блок до следующего заголовка цены
        j, label = i + 1, None
        while j < len(lines) and j < i + 40:
            low = lines[j].strip().lower()
            if re.match(r"^за\s*1\s*млн\s*токенов$", low):
                break
            if low in ("вход", "входящие", "выход", "исходящие"):
                label = low
            elif label:
                price = _money(lines[j])
                if price is not None:
                    unit = "1M токенов (вход)" if label.startswith("вход") else "1M токенов (выход)"
                    offers.append(dict(model=model, unit=unit, price=price, currency="RUB"))
                    label = None
            j += 1
    best: dict[tuple[str, str], dict] = {}
    for off in offers:
        key = (off["model"], off["unit"])
        if key not in best or off["price"] < best[key]["price"]:
            best[key] = off
    return list(best.values())


def fetch_openrouter() -> list[dict]:
    """OpenRouter USD quotes plus catalogue facts, including explicit free routes."""
    data = json.loads(get("https://openrouter.ai/api/v1/models", 60))
    units = {
        "prompt": "1M токенов (вход)",
        "completion": "1M токенов (выход)",
        "input_cache_read": "1M токенов (вход, кэш)",
        "input_cache_write": "1M токенов (кэш-запись)",
    }
    offers = []
    for row in data.get("data", []):
        pricing = row.get("pricing") or {}
        for field, unit in units.items():
            raw = pricing.get(field)
            try:
                val = float(raw)
            except (TypeError, ValueError):
                continue
            if val < 0:
                continue
            # Keep zero-priced offers in the catalogue as explicitly free.
            offers.append(
                dict(
                    model=row.get("id", "?"),
                    unit=unit,
                    price=round(val * 1_000_000, 4),
                    currency="USD",
                    free=val == 0,
                    context_length=row.get("context_length") or 0,
                    max_output=(row.get("top_provider") or {}).get("max_completion_tokens") or 0,
                    input_modalities=(row.get("architecture") or {}).get("input_modalities") or [],
                    output_modalities=(row.get("architecture") or {}).get("output_modalities") or [],
                    supported_parameters=row.get("supported_parameters") or [],
                )
            )
    return offers


FETCHERS = {
    "polza": ("https://polza.ai/models", fetch_polza),
    "routerai": ("https://routerai.ru/models", fetch_routerai),
    "deepseek": ("https://api-docs.deepseek.com/quick_start/pricing/", fetch_deepseek),
    "proxyapi": ("https://proxyapi.ru/pricing", fetch_proxyapi),
    "bothub": ("https://bothub.chat/models", fetch_bothub),
    "jiugx": ("https://jiugx.com/api/pricing", fetch_jiugx),
    "neuroapi": ("https://neuroapi.host/pricing", fetch_neuroapi),
    "openrouter": ("https://openrouter.ai/api/v1/models", fetch_openrouter),
}


# ── сборка ──────────────────────────────────────────────────────────────
# одно и то же под разными именами у поставщиков
ALIASES = {
    # Compare provider endpoint revisions under one customer-facing family name.
    "deepseek-v4.1-flash-1017": "deepseek-v4.1-flash",
    "deepseek-v4.1-flash": "deepseek-v4.1-flash",
    "deepseek-flash": "deepseek-v4.1-flash",
    "deepseek-v4-flash": "deepseek-v4.1-flash",
    "deepseek-v4-pro": "deepseek-v4-pro-0813",
    "deepseek-reasoner": "deepseek-v4-pro-0813",
    "deepseek-chat": "deepseek-v4.1-flash",
}


def model_key(provider: str, model: str) -> str:
    """Одно и то же имя у разных поставщиков → один ключ."""
    slug = model.strip().lower()
    slug = re.sub(r"^[a-z0-9\-]+/", "", slug)  # фирменный префикс пути
    slug = re.sub(r"\s+", "-", slug)
    slug = slug.replace("deepseek/", "")
    if slug in {"deepseek-v4.1-flash", "deepseek-v4.1-flash-1017"}:
        return "deepseek-v4.1-flash"
    # Other aliases remain restricted to the official DeepSeek catalogue:
    # reseller slugs with similar names can refer to different endpoints.
    return ALIASES.get(slug, slug) if provider == "deepseek" else slug


def display_model_name(key: str, fallback: str) -> str:
    if key == "deepseek-v4.1-flash":
        return "DeepSeek V4.1 Flash"
    return fallback


def offer_exclusion(model: str) -> str:
    slug = model.strip().lower().rsplit("/", 1)[-1]
    if slug.endswith(":free"):
        return "free-предложение поставщика, не использовать в платном автопереключении"
    return ""


def build(
    rate: float, rate_source: str, results: dict[str, tuple[bool, str, list[dict]]], limit: int = 0, previous: dict | None = None
) -> dict:
    models: dict[str, dict] = {}
    previous = previous or {}
    old_rows = previous.get("models") or []
    old_keys = {str(row.get("key") or "") for row in old_rows}
    old_first_seen = {str(row.get("key") or ""): row.get("first_seen_at") for row in old_rows}
    old_new = {str(row.get("key") or ""): bool(row.get("new_model")) for row in old_rows}
    providers = []
    for name, (url, _fn) in active_fetchers().items():
        if name not in results:
            continue
        ok, note, offers = results[name]
        providers.append(dict(name=name, url=url, ok=ok, note=note, offers=len(offers)))
        for off in offers:
            key = model_key(name, off["model"])
            rub = off["price"] if off["currency"] == "RUB" else off["price"] * rate
            group = f"{key} · {off['unit']}"  # сравниваем только одинаковые единицы
            entry = models.setdefault(
                group,
                dict(
                    key=key,
                    name=display_model_name(key, off["model"]),
                    unit=off["unit"],
                    group=group,
                    offers=[],
                    context_length=0,
                    max_output=0,
                    input_modalities=[],
                    output_modalities=[],
                    supported_parameters=[],
                    free=False,
                ),
            )
            if key != "deepseek-v4.1-flash" and (not entry["name"] or len(off["model"]) > len(entry["name"])):
                entry["name"] = off["model"]
            entry["context_length"] = max(entry["context_length"], int(off.get("context_length") or 0))
            entry["max_output"] = max(entry["max_output"], int(off.get("max_output") or 0))
            entry["input_modalities"] = sorted(set(entry["input_modalities"]) | set(off.get("input_modalities") or []))
            entry["output_modalities"] = sorted(set(entry["output_modalities"]) | set(off.get("output_modalities") or []))
            entry["supported_parameters"] = sorted(set(entry["supported_parameters"]) | set(off.get("supported_parameters") or []))
            is_free = bool(off.get("free") or off["price"] == 0)
            exclusion = offer_exclusion(str(off["model"]))
            entry["free"] = entry["free"] or is_free
            entry["offers"].append(
                dict(
                    provider=name,
                    unit=off["unit"],
                    rub=round(rub, 4),
                    price=off["price"],
                    currency=off["currency"],
                    free=is_free,
                    model=off["model"],
                    route_eligible=not exclusion,
                    exclusion_reason=exclusion,
                )
            )
    rows = []
    for entry in models.values():
        if not entry["offers"]:
            continue
        eligible_offers = [o for o in entry["offers"] if o["route_eligible"]]
        cheapest = min(eligible_offers or entry["offers"], key=lambda o: float(o["rub"]) if o["rub"] is not None else float("inf"))
        spread = max(o["rub"] for o in entry["offers"]) - min(o["rub"] for o in entry["offers"])
        first_seen = old_first_seen.get(entry["key"]) or datetime.now(UTC).isoformat(timespec="seconds")
        try:
            first_seen_dt = datetime.fromisoformat(first_seen.replace("Z", "+00:00"))
            if first_seen_dt.tzinfo is None:
                first_seen_dt = first_seen_dt.replace(tzinfo=UTC)
        except (AttributeError, TypeError, ValueError):
            first_seen_dt = datetime.now(UTC)
        recently_added = (datetime.now(UTC) - first_seen_dt).total_seconds() <= 14 * 86400
        rows.append(
            dict(
                key=entry["key"],
                name=entry["name"],
                unit=entry["unit"],
                group=entry["group"],
                context_length=entry["context_length"],
                max_output=entry["max_output"],
                input_modalities=entry["input_modalities"],
                output_modalities=entry["output_modalities"],
                supported_parameters=entry["supported_parameters"],
                free=entry["free"],
                first_seen_at=first_seen,
                new_model=entry["key"] not in old_keys or (old_new.get(entry["key"], False) and recently_added),
                offers=sorted(entry["offers"], key=lambda o: o["rub"]),
                cheapest=cheapest["provider"] if eligible_offers else None,
                cheapest_rub=cheapest["rub"] if eligible_offers else None,
                spread=round(spread, 4),
            )
        )
    rows.sort(key=lambda r: r["key"])
    new_models = sorted({r["key"] for r in rows if r["new_model"]})
    changed_keys = []
    old_prices = {
        (r.get("key"), r.get("unit")): tuple((o.get("provider"), round(float(o.get("rub") or 0), 4)) for o in r.get("offers", []))
        for r in old_rows
    }
    for row in rows:
        key = (row["key"], row["unit"])
        current = tuple((o.get("provider"), round(float(o.get("rub") or 0), 4)) for o in row["offers"])
        if key in old_prices and old_prices[key] != current:
            changed_keys.append(row["key"])
    for row in rows:
        row["price_changed"] = row["key"] in changed_keys
    if limit:
        rows = rows[:limit]
    return dict(
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        usd_rub=rate,
        rate_source=rate_source,
        providers=providers,
        changes={
            "new_models": new_models,
            "changed_models": sorted(set(changed_keys)),
            "new_count": len(new_models),
            "changed_count": len(set(changed_keys)),
        },
        models=rows,
    )


# ── доступ из админки ───────────────────────────────────────────────────
SNAPSHOT = Path(__file__).with_name("console") / "providers" / "prices.json"
_LOCK = threading.Lock()
_RUNNING = False


def read_snapshot() -> dict:
    """Что уже собрано. Пустой ответ, пока ни одного снимка нет."""
    try:
        return json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"generated_at": "", "usd_rub": FALLBACK_USD_RUB, "rate_source": "none", "providers": [], "models": [], "pending": _RUNNING}


def refresh_now() -> dict:
    """Собрать заново и записать снимок (синхронно)."""
    previous = read_snapshot()
    rate, rate_source = usd_rub()
    results = {}
    for name, (_url, fn) in active_fetchers().items():
        t0 = time.time()
        try:
            offers = fn()
            ok = bool(offers)
            note = f"{len(offers)} предложений за {time.time() - t0:.0f} с" if offers else "страница прочитана, цен не нашлось"
        except Exception as e:  # noqa: BLE001
            ok, note, offers = False, f"{type(e).__name__}: {e}"[:160], []
        results[name] = (ok, note, offers)
    payload = build(rate, rate_source, results, previous=previous)
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    tmp = SNAPSHOT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(SNAPSHOT)
    return payload


def refresh_async() -> bool:
    """Сбор в фоне (кнопка в админке): True — запустили, False — уже идёт."""
    global _RUNNING
    with _LOCK:
        if _RUNNING:
            return False
        _RUNNING = True

    def work() -> None:
        global _RUNNING
        try:
            refresh_now()
        except Exception:  # noqa: BLE001
            pass
        finally:
            _RUNNING = False

    threading.Thread(target=work, name="provider-prices", daemon=True).start()
    return True


def install_provider_prices(app, admin_dep) -> None:
    """Две ручки админки. Регистрируются из api.py одной строкой — так переживает
    замену api.py другой сессией: достаточно снова вызвать скрипт патча."""
    from fastapi import Depends

    @app.get("/v1/admin/provider-prices", dependencies=[Depends(admin_dep)])
    async def admin_provider_prices() -> dict:
        return read_snapshot()

    def menu_specs() -> tuple:
        settings = getattr(app.state, "settings", None)
        return tuple(getattr(settings, "models", ()) or ())

    @app.post("/v1/admin/provider-prices/refresh", dependencies=[Depends(admin_dep)])
    async def admin_provider_prices_refresh() -> dict:
        return {"started": refresh_async(), "grandet_started": refresh_grandet_async(menu_specs())}

    @app.get("/v1/admin/grandet-prices", dependencies=[Depends(admin_dep)])
    async def admin_grandet_prices() -> dict:
        return read_grandet()

    @app.post("/v1/admin/grandet-prices/refresh", dependencies=[Depends(admin_dep)])
    async def admin_grandet_prices_refresh() -> dict:
        return {"started": refresh_grandet_async(menu_specs())}


# Черновые поставщики: парсеры написаны, но сверка цен показывает систематические
# ошибки привязки модели к цене (карточка съезжает), поэтому по умолчанию выключены.
# Bothub сюда больше не входит: с ключом цены берутся из API, а не из вёрстки.
# ProxyAPI включён: карточки идут подряд, модель определяется по «вендор / slug»,
# а единица — по подписи цены («за 1 секунду», «за 1 минуту»). Видео там дешевле
# Bothub, и без него сравнение показывало одного поставщика.
# Включить остальных для отладки: PRICE_DRAFT_PROVIDERS=1
DRAFT = {"neuroapi"}


def active_fetchers() -> dict:
    if os.environ.get("PRICE_DRAFT_PROVIDERS", "").strip() in ("1", "true", "yes"):
        return dict(FETCHERS)
    return {name: v for name, v in FETCHERS.items() if name not in DRAFT}


# ── рынок Grandet ──────────────────────────────────────────────────────
# Grandet знает чужие релеи и официальные цены вендоров. Снимок считает, где та же
# модель дешевле нашей закупки: официально, у агрегатора или у чужого релея.
GRANDET_BOARD = "https://grandet.ai/api/v1/board"
GRANDET_UA = "grandet-skill/0.3.0 (nanomuse-cloud)"
GRANDET_SKILL_VERSION = "0.3.0"
GRANDET_SKILL_BUILD = "grandet-skill-0.3.0-build.20261018b"
GRANDET_FALLBACK_CNY = 12.5
GRANDET_LOCK = threading.Lock()
GRANDET_RUNNING = False


def cny_rub() -> float:
    """Рубли за юань по ЦБ; недоступен ЦБ — запасной курс."""
    try:
        data = json.loads(get(CBR_URL, 15))
        row = data["Valute"]["CNY"]
        nominal = float(row.get("Nominal") or 1) or 1
        val = float(row["Value"]) / nominal
        if 5 < val < 40:
            return round(val, 4)
    except Exception:  # noqa: BLE001
        pass
    return GRANDET_FALLBACK_CNY


def grandet_path() -> Path:
    """Снимок рядом с prices.json: в проде это смонтированный каталог."""
    base = Path(os.environ.get("PRICES_DATA_DIR") or SNAPSHOT.parent)
    return base / "grandet.json"


def grandet_json(url: str, timeout: int = 40) -> dict:
    """GET с gzip: без него Cloudflare отдаёт большие ответы рывками."""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": GRANDET_UA, "Accept": "application/json", "Accept-Encoding": "gzip"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    return json.loads(body)


def grandet_models(specs: Any) -> list[dict[str, Any]]:
    """Модели меню: id, upstream и наша закупка в юанях за 1M токенов."""
    rows = []
    for spec in specs or ():
        if str(getattr(spec, "kind", "chat")) != "chat":
            continue
        rows.append(
            {
                "key": str(getattr(spec, "id", "")),
                "name": str(getattr(spec, "name", "") or getattr(spec, "id", "")),
                "selector": str(getattr(spec, "upstream", "") or getattr(spec, "id", "")),
                "kind": str(getattr(spec, "kind", "chat")),
                "provider": str(getattr(spec, "provider", "") or ""),
                "context_length": int(getattr(spec, "context_length", 0) or 0),
                "max_output": int(getattr(spec, "max_output", 0) or 0),
                "input_modalities": list(getattr(spec, "input_modalities", ()) or ()),
                "output_modalities": list(getattr(spec, "output_modalities", ()) or ()),
                "lanes": list(getattr(spec, "for_", ()) or ()),
                "recommended": bool(getattr(spec, "recommended", False)),
                "our_in": float(getattr(spec, "price_in", 0) or 0),
                "our_out": float(getattr(spec, "price_out", 0) or 0),
            }
        )
    return [row for row in rows if row["key"] and row["selector"]]


def _grandet_money(value: Any) -> float | None:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _grandet_rows(data: dict, field: str) -> list[dict]:
    rows = data.get("official") if field == "official" else (data.get("listings") or {}).get("inference")
    out = []
    for row in rows or []:
        currency = str(row.get("currency") or "").upper()
        inp, outp = _grandet_money(row.get("input")), _grandet_money(row.get("output"))
        if inp is None or outp is None:
            continue
        out.append(
            {
                "vendor": row.get("vendor"),
                "model_group_id": row.get("modelGroupId"),
                "price_kind": row.get("priceKind"),
                "currency": currency,
                "input": inp,
                "output": outp,
                "cache_read": _grandet_money(row.get("cacheRead")),
                "cache_write": _grandet_money(row.get("cacheWrite")),
                "conditions": row.get("conditions") or [],
                "condition_notes": row.get("conditionNotes") or [],
                "pricing_axis": row.get("pricingAxis"),
                "context_tier": row.get("contextTier"),
                "processing_mode": row.get("processingMode"),
                "aggregator": bool(row.get("aggregator")),
                "comparison_eligible": row.get("comparisonEligible"),
                "cash_percent_vs_official": row.get("cashPercentVsOfficial"),
                "effective_from": row.get("effectiveFrom"),
                "effective_until": row.get("effectiveUntil"),
                "source_url": row.get("sourceUrl"),
                "observed_at": row.get("observedAt"),
            }
        )
    return out


def _grandet_cny(rows: list[dict], usd_cny: float) -> list[dict]:
    """Пересчёт в юани: наша закупка тоже в юанях, так сравнение честнее."""
    out = []
    for row in rows:
        rate = 1.0 if row["currency"] == "CNY" else usd_cny if row["currency"] == "USD" else None
        if not rate:
            continue
        out.append(dict(row, cny_in=round(row["input"] * rate, 4), cny_out=round(row["output"] * rate, 4)))
    return out


def _grandet_market(data: dict, usd_cny: float) -> list[dict]:
    """Доски Grandet: до двух самых дешёвых, с условиями, рисками и интеграцией.

    Кредиты Grandet не равны валюте: цена в юанях у релеев — оценка по
    заявленному курсу (1 кредит = 1 единица валюты), а не подтверждённый счёт.
    """
    out = []
    for board in data.get("boards") or []:
        unit = board.get("unit")
        rate = 1.0 if unit == "CNY_CREDIT" else usd_cny if unit == "USD_CREDIT" else None
        listings = board.get("listings") or {}

        def one(row: dict, rate: float | None = rate) -> dict:
            inp, outp = _grandet_money(row.get("input")), _grandet_money(row.get("output"))
            cache = _grandet_money(row.get("cacheRead"))
            cache_write = _grandet_money(row.get("cacheWrite"))
            integration = row.get("integration") or {}
            listing = row.get("listing") or {}
            return {
                "source_id": row.get("sourceId"),
                "operator": row.get("operator"),
                "model_name": row.get("modelName"),
                "plan": row.get("plan") or row.get("planEn"),
                "provider_category": row.get("providerCategory"),
                "aggregator": bool(row.get("aggregator")),
                "endpoint_types": row.get("endpointTypes") or [],
                "input": inp,
                "output": outp,
                "cache_read": cache,
                "cache_write": cache_write,
                "blended": _grandet_money(row.get("blended")),
                "cny_in": round(inp * rate, 4) if rate and inp is not None else None,
                "cny_out": round(outp * rate, 4) if rate and outp is not None else None,
                "ratio_to_median": row.get("ratioToMedian"),
                "risk_signals": row.get("riskSignals") or [],
                "review_reasons": row.get("reviewReasons") or [],
                "freshness": row.get("freshness"),
                "observed_at": row.get("observedAt"),
                "source_url": row.get("sourceUrl"),
                "board_url": row.get("boardUrl"),
                "listing": {
                    "action": listing.get("action"),
                    "kind": listing.get("kind"),
                    "hint": listing.get("hintEn") or listing.get("hint"),
                    "usage": listing.get("usage") or [],
                },
                "integration": {
                    "base_url": integration.get("baseUrl"),
                    "token_group": integration.get("tokenGroup"),
                    "endpoint_type": integration.get("endpointType"),
                    "profile_id": integration.get("profileId"),
                    "configuration_path": integration.get("configurationPath"),
                    "compatibility": integration.get("compatibility"),
                    "platforms": integration.get("platforms") or [],
                    "docs_url": integration.get("docsUrl"),
                },
                "rank": row.get("rank"),
                "exposure": row.get("exposure"),
                "row_cycle": row.get("rowCycle"),
            }

        out.append(
            {
                "unit": unit,
                "basis": board.get("basis"),
                "status": board.get("status"),
                "totals": listings.get("totals") or {},
                "counts": listings.get("counts") or {},
                "folded": listings.get("folded") or [],
                "lowest": [one(row) for row in (listings.get("lowest") or [])[:2]],
                "gated": [one(row) for row in (listings.get("gated") or [])[:2]],
                "reference": [one(row) for row in (listings.get("reference") or [])[:2]],
            }
        )
    return out


def _grandet_best(model: dict, candidates: list[dict]) -> dict | None:
    """Самое выгодное предложение из тех, что в валюте, и экономия против нашей закупки."""
    if model["kind"] != "chat" or not model["our_in"] or not model["our_out"]:
        return None
    our = model["our_in"] * 1000 + model["our_out"] * 512
    best = None
    for row in candidates:
        cost = row["cny_in"] * 1000 + row["cny_out"] * 512
        if cost <= 0:
            continue
        if best is None or cost < best["raw_cost"]:
            best = dict(row, raw_cost=cost, cost_cny=round(cost / 1_000_000, 6))
    if best is None:
        return None
    best.pop("raw_cost", None)
    best["our_cny"] = round(our / 1_000_000, 6)
    best["savings_pct"] = round((1 - best["cost_cny"] / best["our_cny"]) * 100, 1) if best["our_cny"] else None
    return best


def _grandet_one(model: dict, usd_cny: float) -> tuple[dict, dict | None]:
    """Одна модель: официальные цены, агрегаторы и лучший релей."""
    item = dict(model)
    try:
        url = (
            GRANDET_BOARD
            + "?"
            + urllib.parse.urlencode(
                {
                    "model": model["selector"],
                    "lang": "en",
                    "limit": 5,
                    "view": "compact",
                    "host": "opencode",
                    "targetApp": "opencode",
                    "skillVersion": GRANDET_SKILL_VERSION,
                    "skillBuild": GRANDET_SKILL_BUILD,
                }
            )
        )
        data = grandet_json(url)
        if data.get("code") or data.get("error"):
            item["note"] = f"{data.get('code')}: {str(data.get('message'))[:160]}"
            return item, None
        official = _grandet_cny(_grandet_rows(data, "official"), usd_cny)
        inference = _grandet_cny(_grandet_rows(data, "inference"), usd_cny)
        item["official"] = official
        item["inference"] = inference
        item["market"] = _grandet_market(data, usd_cny)
        item["coverage"] = data.get("coverage") or {}
        item["assumptions"] = data.get("assumptions") or []
        item["notices"] = data.get("notices") or []
        candidates = [dict(row, category="official", provider=row.get("vendor")) for row in official]
        candidates += [dict(row, category="inference", provider=row.get("vendor")) for row in inference]
        candidates += [
            dict(row, category="relay", estimated=True, provider=row.get("source_id"))
            for board in item["market"]
            for row in board.get("lowest") or []
            if row.get("cny_in") and row.get("cny_out")
        ]
        best = _grandet_best(model, candidates)
        if best:
            item["best"] = best
        return item, None
    except Exception as error:  # noqa: BLE001
        return item, {"key": model["key"], "selector": model["selector"], "error": str(error)[:200]}


def grandet_collect(specs: Any) -> dict:
    """Собрать рынок по моделям меню. Курсы ЦБ, ошибки не роняют весь снимок."""
    models = grandet_models(specs)
    usd, usd_source = usd_rub()
    cny = cny_rub()
    usd_cny = round(usd / cny, 4) if cny else 0
    workers = max(1, min(6, len(models)))
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda model: _grandet_one(model, usd_cny), models))
    out_models, errors = [], []
    stats: dict[tuple[str, str], dict] = {}
    for item, error in results:
        if error:
            errors.append(error)
            continue
        out_models.append(item)
        best = item.get("best") or {}
        if best.get("savings_pct") and best["savings_pct"] > 0:
            key = (str(best.get("provider")), str(best.get("category")))
            entry = stats.setdefault(
                key,
                {"provider": key[0], "category": key[1], "wins": 0, "clean_wins": 0, "savings": [], "models": []},
            )
            warned = bool(best.get("risk_signals") or best.get("review_reasons"))
            warned = warned or bool((best.get("listing") or {}).get("usage"))
            entry["wins"] += 1
            entry["clean_wins"] += 0 if warned else 1
            entry["savings"].append(best["savings_pct"])
            entry["models"].append(item["key"])
            if not entry.get("url") and best.get("source_url"):
                entry["url"] = best["source_url"]
            if not entry.get("board_url") and best.get("board_url"):
                entry["board_url"] = best["board_url"]
    stats_rows = []
    for entry in stats.values():
        savings = entry.pop("savings")
        stats_rows.append(
            dict(
                entry,
                avg_savings_pct=round(sum(savings) / len(savings), 1) if savings else None,
                max_savings_pct=max(savings) if savings else None,
            )
        )
    stats_rows.sort(key=lambda row: (-row["wins"], row["provider"] or ""))
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "rates": {"usd_rub": usd, "cny_rub": cny, "usd_cny": usd_cny, "source": usd_source},
        "models": out_models,
        "stats": stats_rows,
        "errors": errors,
    }


def read_grandet() -> dict:
    try:
        return json.loads(grandet_path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"generated_at": "", "models": [], "stats": [], "errors": [], "pending": GRANDET_RUNNING}


def refresh_grandet_now(specs: Any) -> dict:
    payload = grandet_collect(specs)
    path = grandet_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return payload


def refresh_grandet_async(specs: Any) -> bool:
    global GRANDET_RUNNING
    with GRANDET_LOCK:
        if GRANDET_RUNNING:
            return False
        GRANDET_RUNNING = True

    def work() -> None:
        global GRANDET_RUNNING
        try:
            refresh_grandet_now(specs)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        finally:
            GRANDET_RUNNING = False

    threading.Thread(target=work, name="grandet-prices", daemon=True).start()
    return True


def main(argv: list[str]) -> int:
    out = None
    limit = 0
    i = 1
    while i < len(argv):
        if argv[i] in ("--out", "-o"):
            out = argv[i + 1]
            i += 2
        elif argv[i] == "--limit":
            limit = int(argv[i + 1])
            i += 2
        else:
            i += 1
    rate, rate_source = usd_rub()
    results = {}
    for name, (_url, fn) in active_fetchers().items():
        t0 = time.time()
        try:
            offers = fn()
            ok, note = True, f"{len(offers)} предложений за {time.time() - t0:.0f} с"
            if not offers:
                ok, note = False, "страница прочитана, цен не нашлось"
        except Exception as e:  # noqa: BLE001
            ok, note, offers = False, f"{type(e).__name__}: {e}"[:160], []
        results[name] = (ok, note, offers)
        print(f"{name:<10} {'ok ' if ok else 'нет'} {len(offers):>4} цен  {note}")
    payload = build(rate, rate_source, results, limit)
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"{path}: моделей {len(payload['models'])}, курс $ = {rate} ₽ ({rate_source})")
    else:
        print(text[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
