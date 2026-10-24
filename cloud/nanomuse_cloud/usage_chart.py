"""Account-only retail usage series. Never uses provider cost as a second wallet."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, HTTPException, Query, Response

from .model_accounting import _rub


def chart(cloud: Any, account_id: str, period: str, zone: str) -> dict:
    try:
        tz = ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "Unknown timezone") from None
    now = int(cloud.clock())
    local_now = datetime.fromtimestamp(now, tz)
    if period == "day":
        start = now // 3600 * 3600 - 23 * 3600
        buckets = [start + i * 3600 for i in range(24)]
    else:
        count = 7 if period == "week" else 30
        first = local_now.date() - timedelta(days=count - 1)
        buckets = [int(datetime.combine(first + timedelta(days=i), datetime.min.time(), tz).timestamp()) for i in range(count)]
        start = buckets[0]
    grouped: dict[str, dict] = {}
    with cloud.db._lock:
        conn = cloud.db._conn
        columns = {r[1] for r in conn.execute("PRAGMA table_info(ledger)")}
        price = "sale_uy" if "sale_uy" in columns else "cost_uy"
        rows = conn.execute(
            f"SELECT ts,model,prompt_tokens,completion_tokens,{price} amount FROM ledger WHERE account_id=? AND ts>=? AND ts<=? AND charged>=0 AND kind IN ('chat','image','video','realtime')",
            (account_id, start, now),
        ).fetchall()
    free_requests = 0
    requests = len(rows)
    tokens = sum(max(0, int(r["prompt_tokens"] or 0)) + max(0, int(r["completion_tokens"] or 0)) for r in rows)
    for row in rows:
        amount = int(row["amount"] or 0)
        if amount <= 0:
            free_requests += 1
            continue
        if period == "day":
            index = (int(row["ts"]) - start) // 3600
        else:
            index = (datetime.fromtimestamp(int(row["ts"]), tz).date() - first).days
        if not 0 <= index < len(buckets):
            continue
        model = str(row["model"] or "")
        data = grouped.setdefault(model, {"raw": [0] * len(buckets), "requests": 0})
        data["raw"][index] += amount
        data["requests"] += 1
    series = []
    for model, data in grouped.items():
        series.append(
            {
                "model": model,
                "values": [_rub(cloud, n) for n in data["raw"]],
                "total_rub": _rub(cloud, sum(data["raw"])),
                "requests": data["requests"],
            }
        )
    series.sort(key=lambda r: (-r["total_rub"], r["model"]))
    return {
        "period": period,
        "timezone": zone,
        "currency": "RUB",
        "start": start,
        "end": now,
        "buckets": buckets,
        "series": series,
        "total_rub": _rub(cloud, sum(sum(d["raw"]) for d in grouped.values())),
        "free_requests": free_requests,
        "requests": requests,
        "tokens": tokens,
        "basis": "retail_ledger" if price == "sale_uy" else "legacy_config_ledger",
    }


def install(app: Any, cloud: Any, caller_dep: Any) -> None:
    @app.get("/v1/me/usage-chart")
    async def own_chart(
        response: Response,
        period: Literal["day", "week", "month"] = "week",
        tz: str = Query("UTC", min_length=1, max_length=64),
        caller: Any = Depends(caller_dep),  # noqa: B008
    ) -> dict:
        response.headers["Cache-Control"] = "no-store"
        return chart(cloud, caller.account_id, period, tz)
