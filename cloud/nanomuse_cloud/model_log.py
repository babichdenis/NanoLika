"""One model's history for the operator: prices, visibility and human notes.

The two-pane dialog in the admin is deliberately small: the top table is the
timeline, the bottom pane prints the selected line in full. System entries come
from `control_audit` (the same audit the operator already trusts); the light
green ones are `model_notes`, written from the dialog.

Routes:

    GET  /v1/admin/models/{model_id}/log   — timeline + model facts
    POST /v1/admin/models/{model_id}/notes — add a note
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, HTTPException

RATE_LABELS = {
    "input": "вход",
    "output": "выход",
    "cached_input": "кэш",
    "image": "картинка",
    "image_2k": "картинка 2K",
    "image_input": "вход картинки",
    "second": "секунда",
    "speech": "1000 знаков",
    "minute": "минута",
}


def _money(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number:,.2f}".replace(",", " ").replace(".", ",")


def _rates_text(rates: Any) -> str:
    if not isinstance(rates, dict):
        return ""
    parts = []
    for key, label in RATE_LABELS.items():
        value = rates.get(key)
        if value:
            parts.append(f"{label} {_money(value)} ₽")
    return " · ".join(parts)


def _audit_event(row: Any) -> dict[str, Any]:
    action = str(row["action"] or "")
    detail = str(row["detail"] or "")
    title = action
    body = detail
    try:
        data = json.loads(detail) if detail else {}
    except (ValueError, TypeError):
        data = {}
    if action == "model.price":
        title = "Цена опубликована"
        rates = data.get("rates_rub") if isinstance(data, dict) else None
        body = _rates_text(rates) or detail
    elif action == "model.price.draft":
        title = "Цена в черновике"
        body = _rates_text(data if isinstance(data, dict) else {}) or detail
    elif action == "model.price.draft.clear":
        title = "Черновик снят"
        body = "Неопубликованная правка цены удалена."
    elif action == "model.visibility":
        visible = data.get("visible") if isinstance(data, dict) else None
        title = "Видимость изменена"
        body = "Модель показана клиентам." if visible else "Модель скрыта от клиентов."
    elif action.startswith("model."):
        title = action.replace("model.", "").replace(".", " ")
    return {
        "id": f"a{row['id']}",
        "at": int(row["ts"]),
        "kind": "system",
        "action": action,
        "title": title[:120],
        "body": body,
        "author": str(row["actor"] or "")[:80],
    }


def _note_event(row: Any) -> dict[str, Any]:
    return {
        "id": f"n{row['id']}",
        "at": int(row["at"]),
        "kind": "note",
        "action": "comment",
        "title": "Заметка",
        "body": str(row["body"] or ""),
        "author": str(row["author"] or "")[:80],
    }


def _seen_event(cloud: Any, spec: Any) -> dict[str, Any] | None:
    try:
        from .prices_admin import _dir, _first_seen, _lookup_seen

        prices = json.loads((_dir() / "prices.json").read_text(encoding="utf-8"))
        found = _lookup_seen(_first_seen(prices), getattr(spec, "upstream", ""), getattr(spec, "id", ""))
    except (OSError, ValueError, TypeError):
        return None
    if not found:
        return None
    day = str(found.get("at") or "")
    if not day:
        return None
    try:
        at = int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp())
    except ValueError:
        return None
    return {
        "id": "seen",
        "at": at,
        "kind": "system",
        "action": "seen",
        "title": "Появление у поставщиков",
        "body": f"Впервые в снимке прайсов: {day[8:10]}.{day[5:7]}.{day[:4]}.",
        "author": "",
    }


def install(app: Any, settings: Any, admin_dep: Any, *, cloud: Any = None) -> None:
    @app.get("/v1/admin/models/{model_id}/log", dependencies=[Depends(admin_dep)])
    async def model_log(model_id: str) -> dict:
        spec = settings.model(model_id)
        if cloud is None or spec is None:
            raise HTTPException(404, "Unknown model")
        events = [_audit_event(row) for row in cloud.db.audit_for_target(spec.id, 200)]
        events += [_note_event(row) for row in cloud.db.model_notes(spec.id, 200)]
        seen = _seen_event(cloud, spec)
        if seen:
            events.append(seen)
        events.sort(key=lambda item: (item["at"], item["id"]), reverse=True)
        return {
            "model": {
                "id": spec.id,
                "name": spec.name,
                "kind": spec.kind,
                "listed": cloud.model_visible(spec),
            },
            "events": events,
        }

    @app.post("/v1/admin/models/{model_id}/notes", dependencies=[Depends(admin_dep)])
    async def model_note(model_id: str, body: dict) -> dict:
        spec = settings.model(model_id)
        if cloud is None or spec is None:
            raise HTTPException(404, "Unknown model")
        text = str((body or {}).get("body") or "").strip()
        if not text:
            raise HTTPException(422, "Note is empty")
        row = cloud.db.model_note_add(spec.id, text[:4000])
        return _note_event(row)
