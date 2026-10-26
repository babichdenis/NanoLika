"""One retail price source for the catalogue, estimates and settlement.

Provider costs remain separate. Operator edits take effect on the next request and are
stored in SQLite; already submitted video tasks retain their accepted sale amount.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from typing import Any

KEY = "model_retail_prices"
FIELDS = {
    "input",
    "cached_input",
    "output",
    "image",
    "image_2k",
    "image_input",
    "second",
    "speech",
    "minute",
}


def policy(cloud: Any, spec: Any) -> dict:
    stored = cloud.db.settings_get(KEY)
    all_rows = stored[0] if stored and isinstance(stored[0], dict) else {}
    row = all_rows.get(spec.id) or {}
    s = cloud.s

    def to_rub(cny: float) -> float:
        return (
            cny / s.usd_cny * s.rub_per_usd * (1 + s.topup_fee_pct / 100)
            if s.usd_cny and s.rub_per_usd
            else 0.0
        )

    rates = {
        "input": spec.sell_in or to_rub(spec.price_in),
        "cached_input": spec.sell_cached_in
        or spec.sell_in
        or to_rub(spec.price_in * spec.cached_in_rate),
        "output": spec.sell_out or spec.sell_in or to_rub(spec.price_out),
        "image": to_rub(spec.price_image),
        "image_2k": to_rub(spec.price_image_2k or spec.price_image),
        "image_input": to_rub(spec.price_image_in),
        "second": to_rub(spec.price_second),
        "speech": to_rub(spec.price_speech),
        "minute": to_rub(spec.price_minute),
    }
    rates.update(row.get("rates_rub") or {})
    return {"revision": int(row.get("revision") or 0), "rates_rub": rates, "source": "operator" if row or (spec.kind == "chat" and spec.sells_at_own_rate()) else "legacy_cost"}


def retail_spec(cloud: Any, spec: Any) -> Any:
    stored = cloud.db.settings_get(KEY)
    rows = stored[0] if stored and isinstance(stored[0], dict) else {}
    rates = (rows.get(spec.id) or {}).get("rates_rub") or {}
    changes = {
        field: rates[key]
        for key, field in (
            ("input", "sell_in"),
            ("cached_input", "sell_cached_in"),
            ("output", "sell_out"),
        )
        if key in rates
    }
    return replace(spec, **changes) if changes else spec


def describe(cloud: Any, spec: Any, caller: Any = None) -> dict:
    result = policy(cloud, spec)
    pct = cloud.spend_discount_pct(caller.account_id) if caller else 0
    result.update(
        currency="RUB",
        discount_pct=pct,
        effective_rub={k: round(v * (100 - pct) / 100, 6) for k, v in result["rates_rub"].items()},
        context_length=spec.context_length,
        max_output=spec.max_output,
        unit={"chat": "million_tokens", "image": "image", "video": "second"}.get(
            spec.kind, spec.audio_kind
        ),
        controls=controls(spec),
    )
    return result


def controls(spec: Any) -> list[dict]:
    if spec.kind == "chat":
        return [
            {"key": "input_tokens", "label": "Токены запроса", "type": "number", "default": 1000},
            {
                "key": "output_tokens",
                "label": "Токены ответа",
                "type": "number",
                "default": min(spec.max_output or 512, 512),
            },
        ]
    if spec.kind == "image":
        sizes = ["1024x1024"]
        if spec.price_image_2k:
            sizes.append("2048x2048")
        return [
            {
                "key": "size",
                "label": "Размер",
                "type": "select",
                "options": sizes,
                "default": sizes[0],
            }
        ]
    if spec.kind == "video":
        return [
            {
                "key": "seconds",
                "label": "Длительность, сек.",
                "type": "number",
                "default": spec.clip_seconds,
            },
            {
                "key": "resolution",
                "label": "Разрешение",
                "type": "select",
                "options": ["480P", *dict(spec.price_second_res)],
                "default": "480P",
            },
        ]
    if spec.audio_kind == "speech":
        return [{"key": "characters", "label": "Знаков текста", "type": "number", "default": 1000}]
    if spec.audio_kind == "transcribe":
        return [{"key": "seconds", "label": "Длительность, сек.", "type": "number", "default": 60}]
    return []


def amount(cloud: Any, spec: Any, params: dict, caller: Any = None) -> dict:
    from .service import CloudError

    price_policy = policy(cloud, spec)
    rates = price_policy["rates_rub"]

    def num(key: str, default: float = 0, maximum: float = 10_000_000) -> float:
        value = params.get(key, default)
        if isinstance(value, bool):
            raise CloudError(400, "bad_quote", "Invalid quantity")
        try:
            n = float(value)
        except (TypeError, ValueError) as exc:
            raise CloudError(400, "bad_quote", "Invalid quantity") from exc
        if not math.isfinite(n) or not 0 <= n <= maximum:
            raise CloudError(400, "bad_quote", "Quantity out of range")
        return n

    if spec.kind == "chat":
        inp, out, cached = (
            num("input_tokens", 1000),
            num("output_tokens", 512),
            num("cached_tokens"),
        )
        if cached > inp:
            raise CloudError(400, "bad_quote", "Cached tokens exceed input")
        if spec.context_length and inp + out > spec.context_length:
            raise CloudError(400, "bad_quote", "Context limit exceeded")
        if spec.max_output and out > spec.max_output:
            raise CloudError(400, "bad_quote", "Output limit exceeded")
        rub = (
            (inp - cached) * rates["input"] + cached * rates["cached_input"] + out * rates["output"]
        ) / 1_000_000
    elif spec.kind == "image":
        size = str(params.get("size") or "1024x1024").replace("*", "x")
        # Existing rectangular sizes at the base/2k tier remain supported by older clients.
        try:
            width, height = [int(x) for x in size.split("x")]
        except (TypeError, ValueError) as exc:
            raise CloudError(400, "bad_quote", "Invalid image size") from exc
        if min(width, height) <= 0 or max(width, height) > 4096:
            raise CloudError(400, "bad_quote", "Invalid image size")
        rub = rates["image_2k" if max(width, height) > 1024 else "image"] * num("count", 1, 50)
        rub += rates["image_input"] * num("inputs", 0, 50)
    elif spec.kind == "video":
        resolution = str(params.get("resolution") or "480P").upper()
        second = rates["second"]
        tiers = dict(spec.price_second_res)
        if resolution in tiers and spec.price_second:
            second *= tiers[resolution] / spec.price_second
        elif resolution not in {"480P", "720P", "768P", "1080P", "4K"}:
            raise CloudError(400, "bad_quote", "Resolution is not priced")
        rub = second * num("seconds", spec.clip_seconds, 300)
    elif spec.audio_kind == "speech":
        rub = rates["speech"] * max(1, math.ceil(num("characters", 1000) / 1000))
    elif spec.audio_kind == "transcribe":
        rub = rates["minute"] * max(1, math.ceil(num("seconds", 60) / 60))
    else:
        raise CloudError(400, "bad_quote", "This generation is not connected")
    pct = cloud.spend_discount_pct(caller.account_id) if caller else 0
    base_uy = cloud.s.rub_to_uy(rub)
    # Older self-hosted relays may still account in CNY without a ruble display rate.
    if cloud.s.rub_per_usd <= 0 or cloud.s.usd_cny <= 0:
        if spec.kind == "chat":
            base_uy = spec.chat_cost_uy(int(inp), int(out), int(cached))
        elif spec.kind == "image":
            base_uy = spec.image_cost_uy(str(params.get("size") or "1024x1024"), int(num("inputs", 0, 50))) * int(num("count", 1, 50))
        elif spec.kind == "video":
            base_uy = spec.video_cost_uy(num("seconds", spec.clip_seconds, 300), str(params.get("resolution") or "480P"))
        elif spec.audio_kind == "speech":
            base_uy = round(spec.price_speech * max(1, math.ceil(num("characters", 1000) / 1000)) * 1_000_000)
        elif spec.audio_kind == "transcribe":
            base_uy = round(spec.price_minute * max(1, math.ceil(num("seconds", 60) / 60)) * 1_000_000)

    sale_uy = cloud.discount(base_uy, caller.account_id) if caller else base_uy
    return {
        "model": spec.id,
        "revision": price_policy["revision"],
        "currency": "RUB",
        "base_rub": round(rub, 6),
        "discount_pct": pct,
        "total_rub": round(rub * (100 - pct) / 100, 6),
        "sale_uy": sale_uy,
        "estimate": True,
        "excludes": ["tools", "additional_reasoning"] if spec.kind == "chat" else [],
    }


def save(cloud: Any, spec: Any, body: dict) -> dict:
    from .service import CloudError

    values = body.get("rates_rub")
    if not isinstance(values, dict) or not values or set(values) - FIELDS:
        raise CloudError(400, "bad_price", "Provide known rates_rub fields")
    clean = {}
    for key, value in values.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 < value <= 10_000_000
        ):
            raise CloudError(400, "bad_price", "Rates must be finite positive ruble amounts")
        clean[key] = round(value, 6)
    with cloud.db.tx() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
        data = json.loads(row[0]) if row else {}
        old = data.get(spec.id) or {}
        if body.get("revision") != int(old.get("revision") or 0):
            raise CloudError(409, "price_changed", "Reload prices before saving")
        current = {
            "revision": int(old.get("revision") or 0) + 1,
            "rates_rub": {**(old.get("rates_rub") or {}), **clean},
        }
        data[spec.id] = current
        conn.execute(
            "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (KEY, json.dumps(data), cloud.clock()),
        )
    cloud.db.audit_add("console", "model.price", spec.id, json.dumps(current))
    return describe(cloud, spec)
