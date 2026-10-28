"""Маршруты поставщиков из каталога базы моделей.

Каталог собирает проект modelscope (`export.py`) и кладёт рядом со страницами:
`/srv/models/api/catalog.json`. Здесь он только читается — релей ничего не
пересчитывает и не выбирает «на глаз»: порядок кандидатов уже отсортирован по
закупочной цене, а первыми идут цены в той же единице, что и цена клиента.

Файл перечитывается по mtime: база обновляется раз в сутки, рестарт не нужен.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PATH = Path(os.environ.get("MODELS_CATALOG") or "/srv/models/api/catalog.json")
_cache: dict[str, Any] = {"mtime": None, "data": {}}


def load() -> dict:
    """Каталог целиком; пустой словарь, если файла нет или он битый."""
    try:
        mtime = PATH.stat().st_mtime
    except OSError:
        return {}
    if _cache["mtime"] == mtime:
        return _cache["data"]
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        log.warning("каталог моделей не прочитан (%s): %s", PATH, error)
        return {}
    if not isinstance(data, dict):
        return {}
    _cache["mtime"] = mtime
    _cache["data"] = data
    return data


def slug(alias: str) -> str | None:
    """Внутреннее имя приложения (`image`) → слаг базы."""
    return (load().get("menu") or {}).get(alias)


def candidates(alias_or_slug: str) -> list[dict]:
    """Поставщики модели по возрастанию закупки."""
    data = load()
    key = (data.get("menu") or {}).get(alias_or_slug, alias_or_slug)
    model = (data.get("models") or {}).get(key) or {}
    return list(model.get("candidates") or [])


def retail(alias_or_slug: str) -> dict | None:
    """Цена клиенту: {"price": 16.0, "unit": "per_image", "from": false}."""
    data = load()
    key = (data.get("menu") or {}).get(alias_or_slug, alias_or_slug)
    model = (data.get("models") or {}).get(key) or {}
    price = model.get("retail") or {}
    return price if price.get("price") is not None else None


# у кого реально есть POST /images/generations: проверено запросом
# 28.10.2026 — polza (свой адаптер), bothub, routerai, aitunnel.
# У openrouter и proxyapi такого маршрута нет (404): картинки они отдают
# только через чат, и релею они в переборе не годятся.
IMAGE_PROVIDERS = frozenset({"polza", "bothub", "routerai", "aitunnel"})


def image_candidates(alias: str) -> list[dict]:
    """Кандидаты для картинок в порядке цены.

    Сначала те, у кого цена за штуку (её видно и можно сравнить), затем
    остальные — как резерв: polza, например, считает картинку по токенам и
    в каталоге стоит в ₽/1M. Порядок внутри уже задан экспортом.
    """
    return [c for c in candidates(alias)
            if c.get("provider") in IMAGE_PROVIDERS and c.get("model")]
