"""Черновики розничных цен: оператор видит «было → станет», клиенты — только после публикации.

Живые цены лежат в `model_retail_prices` (`model_pricing.KEY`). Здесь — второй
слой: ставки, которые оператор уже набрал, но ещё не показал клиентам. Черновик
хранит полный набор ставок (текущие + изменённые поля), ревизию опубликованной
цены на момент набора и само изменение. Публикация проверяет, что опубликованная
ревизия не сдвинулась: если цену успели изменить в другой вкладке, черновик
помечается устаревшим и не затирает чужую правку.

Маршруты живут в `prices_admin.py`; этот модуль — только хранение и проверки.
"""

from __future__ import annotations

import json
import math
from typing import Any

from . import model_pricing

KEY = "model_retail_drafts"


def _clean(values: Any) -> dict[str, float]:
    from .service import CloudError

    if not isinstance(values, dict) or not values or set(values) - model_pricing.FIELDS:
        raise CloudError(400, "bad_price", "Provide known rates_rub fields")
    clean: dict[str, float] = {}
    for key, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 10_000_000:
            raise CloudError(400, "bad_price", "Rates must be finite positive ruble amounts")
        clean[key] = round(float(value), 6)
    return clean


def all_rows(cloud: Any) -> dict[str, dict[str, Any]]:
    """Все черновики одним чтением: страница цен не должна ходить в базу 500 раз."""
    stored = cloud.db.settings_get(KEY)
    data = stored[0] if stored and isinstance(stored[0], dict) else {}
    return {str(key): value for key, value in data.items() if isinstance(value, dict)}


def describe(commerce: dict[str, Any], draft: dict[str, Any] | None) -> dict[str, Any] | None:
    """Черновик для строки таблицы: что меняется, было и станет."""
    if not draft:
        return None
    current = commerce.get("rates_rub") or {}
    changed = draft.get("changed") if isinstance(draft.get("changed"), dict) else {}
    before = {key: current.get(key) for key in changed}
    return {
        "changed": changed,
        "before": before,
        "rates_rub": draft.get("rates_rub") or {},
        "base_revision": int(draft.get("base_revision") or 0),
        "at": draft.get("at"),
        "stale": int(commerce.get("revision") or 0) != int(draft.get("base_revision") or 0),
    }


def stage(cloud: Any, spec: Any, values: Any, *, note: str = "") -> dict[str, Any] | None:
    """Положить ставки в черновик поверх текущей опубликованной цены.

    Если после наложения ничего не изменилось, черновик снимается: держать
    «изменение», равное текущей цене, значит показывать оператору ложный сигнал.
    """
    clean = _clean(values)
    current = model_pricing.policy(cloud, spec)
    rates = current["rates_rub"]
    with cloud.db.tx() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
        data = json.loads(row[0]) if row else {}
        old = data.get(spec.id) if isinstance(data.get(spec.id), dict) else {}
        # Правки строки приходят по одной (сначала вход, потом выход). Накладываем
        # их на уже существующий черновик, а не на опубликованную цену: иначе
        # вторая правка молча выбрасывала первую.
        same_base = old and int(old.get("base_revision") or 0) == int(current["revision"])
        changed = dict(old.get("changed") or {}) if same_base else {}
        changed.update(clean)
        changed = {key: value for key, value in changed.items() if abs(float(rates.get(key) or 0) - value) > 1e-9}
        if not changed:
            data.pop(spec.id, None)
            conn.execute(
                "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                (KEY, json.dumps(data), cloud.clock()),
            )
            cloud.db.audit_add("console", "model.price.draft.clear", spec.id, "{}")
            return None
        draft = {
            "base_revision": int(current["revision"]),
            "rates_rub": {**rates, **changed},
            "changed": changed,
            "at": cloud.clock(),
            "note": note,
        }
        data[spec.id] = draft
        conn.execute(
            "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (KEY, json.dumps(data), cloud.clock()),
        )
    cloud.db.audit_add("console", "model.price.draft", spec.id, json.dumps(changed))
    return draft


def drop(cloud: Any, model_id: str) -> bool:
    with cloud.db.tx() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
        data = json.loads(row[0]) if row else {}
        existed = data.pop(model_id, None) is not None
        conn.execute(
            "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (KEY, json.dumps(data), cloud.clock()),
        )
    if existed:
        cloud.db.audit_add("console", "model.price.draft.clear", model_id, "{}")
    return existed


def discard(cloud: Any, ids: list[str] | None = None) -> int:
    with cloud.db.tx() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
        data = json.loads(row[0]) if row else {}
        before = len(data)
        if ids is None:
            data = {}
        else:
            for model_id in ids:
                data.pop(str(model_id), None)
        conn.execute(
            "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (KEY, json.dumps(data), cloud.clock()),
        )
    removed = before - len(data)
    if removed:
        cloud.db.audit_add("console", "model.price.draft.discard", "", json.dumps({"removed": removed}))
    return removed


def publish(cloud: Any, settings: Any, ids: list[str] | None = None) -> dict[str, Any]:
    """Опубликовать черновики: сначала проверка ревизий, потом запись цен.

    Проверяем все выбранные строки до первой записи, чтобы одна устаревшая
    цена не оставила пачку наполовину опубликованной.
    """
    data = all_rows(cloud)
    targets = [str(model_id) for model_id in (ids if ids is not None else data.keys())]
    ready: list[tuple[Any, dict[str, Any]]] = []
    failed: list[dict[str, str]] = []
    for model_id in targets:
        draft = data.get(model_id)
        spec = settings.model(model_id) if model_id else None
        if spec is None:
            failed.append({"id": model_id, "error": "unknown model"})
            continue
        if not draft:
            failed.append({"id": model_id, "error": "нет черновика"})
            continue
        current = model_pricing.policy(cloud, spec)
        if int(current["revision"]) != int(draft.get("base_revision") or 0):
            failed.append({"id": model_id, "error": "цена изменилась после черновика — перечитайте"})
            continue
        ready.append((spec, draft))
    published: list[str] = []
    for spec, draft in ready:
        try:
            model_pricing.save(
                cloud,
                spec,
                {
                    "revision": int(draft.get("base_revision") or 0),
                    "rates_rub": draft.get("changed") or {},
                },
            )
            published.append(spec.id)
        except Exception as error:  # noqa: BLE001
            failed.append({"id": spec.id, "error": str(error)[:160]})
    if published:
        discard(cloud, published)
        cloud.db.audit_add("console", "model.price.publish", "", json.dumps({"published": len(published)}))
    return {"published": len(published), "total": len(targets), "failed": failed[:20], "ids": published[:2000]}
