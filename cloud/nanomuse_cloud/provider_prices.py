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
import html
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

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
    """polza.ai: у каждой модели своя страница, цена с единицей (₽).

    На странице три разных вида записи:
      «11,56 ₽ | Запрос / 1М | 17,16 ₽ | Ответ / 1М»   — текст, за 1M токенов;
      «от 3 до 6,75 ₽/ изобр.»                          — картинка, диапазон;
      «0,24 ₽ | Минута»                                 — речь по минутам.
    """
    data = json.loads(get("https://polza.ai/api/v1/models", 60))
    slugs = [m["id"] for m in data.get("data", [])]
    if limit:
        slugs = slugs[:limit]

    rec_token = re.compile(r"([\d\u00a0 ]+(?:[.,]\d+)?)\s*₽\s*(Запрос|Ответ)\s*/\s*1М", re.I)
    rec_range = re.compile(
        r"от\s*([\d\u00a0 ]+(?:[.,]\d+)?)\s*до\s*([\d\u00a0 ]+(?:[.,]\d+)?)\s*₽\s*/\s*"
        r"(изобр|сек|видео|картин)",
        re.I,
    )
    rec_unit = re.compile(
        r"([\d\u00a0 ]+(?:[.,]\d+)?)\s*₽\s*(Изображение|Видео|Секунда|Минута|"
        r"Символ|Запрос|1М символов)",
        re.I,
    )

    def one(slug: str) -> list[dict]:
        try:
            page = get(f"https://polza.ai/models/{slug}", 20)
        except Exception:  # noqa: BLE001
            return []
        flat = re.sub(r"\s+", " ", text_of(page))
        out = []
        for m in rec_token.finditer(flat):
            price = number(m.group(1))
            if price:
                kind = "вход" if m.group(2).lower() == "запрос" else "выход"
                out.append(dict(model=slug, unit=f"1M токенов ({kind})", price=price, currency="RUB"))
        for m in rec_range.finditer(flat):
            low, high = number(m.group(1)), number(m.group(2))
            unit = (
                "секунда"
                if m.group(3).lower() == "сек"
                else "изображение"
                if m.group(3).lower().startswith(("изобр", "картин"))
                else "видео"
            )
            for price in (low, high):
                if price:
                    out.append(dict(model=slug, unit=unit, price=price, currency="RUB"))
        for m in rec_unit.finditer(flat):
            price = number(m.group(1))
            raw = m.group(2).lower()
            unit = (
                "изображение"
                if raw.startswith("изображ")
                else "видео"
                if raw == "видео"
                else "секунда"
                if raw == "секунда"
                else "минута"
                if raw == "минута"
                else "1M символов"
                if "символ" in raw
                else "запрос"
            )
            if price:
                out.append(dict(model=slug, unit=unit, price=price, currency="RUB"))
        # «Запрос» на карточке — та же цена, что вход за 1M токенов: не дублируем
        inflow = {o["price"] for o in out if o["unit"] == "1M токенов (вход)"}
        out = [o for o in out if not (o["unit"] == "запрос" and o["price"] in inflow)]
        # На странице модели polza бывает несколько тарифов одной единицы
        # (разные провайдеры, кэш, разрешения). Для сравнения берём самый дешёвый.
        best: dict[str, dict] = {}
        for o in out:
            cur = best.get(o["unit"])
            if cur is None or o["price"] < cur["price"]:
                best[o["unit"]] = o
        return list(best.values())

    offers: list[dict] = []
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        for rows in pool.map(one, slugs):
            offers.extend(rows)
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
    «Ввод | 632 ₽ | за 1М токенов», имя модели стоит выше, как «vendor/slug»."""
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
        if not (low.startswith("ввод") or low.startswith("вывод") or low.startswith("изображени")):
            continue
        tail = " ".join(lines[i : i + 3])
        price = _money(tail)
        if price is None or "токен" not in tail.lower() and "изображени" not in low:
            continue
        unit = "1M токенов (вход)" if low.startswith("ввод") else "1M токенов (выход)" if low.startswith("вывод") else "изображение"
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
        for price in model.get("prices") or []:
            unit = BOTHUB_UNITS.get((price.get("unit"), price.get("key")))
            if unit is None:
                unit = BOTHUB_UNITS.get((price.get("unit"), None))
            if unit is None:
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

    @app.post("/v1/admin/provider-prices/refresh", dependencies=[Depends(admin_dep)])
    async def admin_provider_prices_refresh() -> dict:
        return {"started": refresh_async()}


# Черновые поставщики: парсеры написаны, но сверка цен показывает систематические
# ошибки привязки модели к цене (карточка съезжает), поэтому по умолчанию выключены.
# Bothub сюда больше не входит: с ключом цены берутся из API, а не из вёрстки.
# Включить остальных для отладки: PRICE_DRAFT_PROVIDERS=1
DRAFT = {"proxyapi", "neuroapi"}


def active_fetchers() -> dict:
    if os.environ.get("PRICE_DRAFT_PROVIDERS", "").strip() in ("1", "true", "yes"):
        return dict(FETCHERS)
    return {name: v for name, v in FETCHERS.items() if name not in DRAFT}


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
