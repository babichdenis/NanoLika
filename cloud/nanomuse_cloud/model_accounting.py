"""Read-only statements. The existing ledger remains the sole balance authority."""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import Depends, Query, Response

KINDS = "('chat','image','video','realtime')"


def capture_metadata(
    headers: Any, obj: Any = None, *, provider: str = "",
    usd_rub: float = 0, usd_fee_pct: float = 0,
) -> dict:
    meta = {}
    gateway_id = headers.get("x-oneapi-request-id", "")
    if isinstance(gateway_id, str) and gateway_id and len(gateway_id) <= 160:
        meta["gateway_request_id"] = gateway_id
    if isinstance(obj, dict):
        model = obj.get("model")
        if isinstance(model, str) and model:
            meta["upstream_model"] = model[:160]
        usage = obj.get("usage")
        amount = usage.get("cost_rub") if isinstance(usage, dict) else None
        if amount is None and provider in {"polza", "routerai", "aitunnel"} and isinstance(usage, dict):
            amount = usage.get("cost")  # These suppliers publish usage.cost in RUB.
        inference_provider = obj.get("provider")
        if isinstance(inference_provider, str) and inference_provider:
            meta["inference_provider"] = inference_provider[:160]
        if amount is None and provider == "openrouter" and isinstance(usage, dict):
            raw_usd = usage.get("cost")
            if not isinstance(raw_usd, bool):
                try:
                    usd = float(raw_usd)
                    if math.isfinite(usd) and 0 <= usd <= 10_000 and usd_rub > 0:
                        meta["provider_cost_usd"] = usd
                        meta["provider_fx_rub_per_usd"] = usd_rub
                        meta["provider_funding_fee_pct"] = usd_fee_pct
                        meta["provider_cost_basis"] = "reported_usd_converted"
                        amount = usd * usd_rub * (1 + usd_fee_pct / 100)
                except (ValueError, TypeError, OverflowError):
                    pass
        if not isinstance(amount, bool) and isinstance(amount, (int, float, str)):
            try:
                value = float(amount)
                if math.isfinite(value) and 0 <= value <= 1_000_000:
                    meta["provider_cost_rub"] = value
            except (ValueError, OverflowError):
                pass
    return meta


def record_metadata(cloud: Any, account_id: str, request_id: str, meta: dict) -> None:
    if not meta:
        return
    try:
        with cloud.db.tx() as conn:
            row = conn.execute(
                "SELECT id,extra FROM ledger WHERE account_id=? AND request_id=? AND kind='chat' ORDER BY id DESC LIMIT 1",
                (account_id, request_id),
            ).fetchone()
            if row is None:
                return
            try:
                extra = json.loads(row["extra"] or "{}")
            except (ValueError, TypeError):
                extra = {}
            if not isinstance(extra, dict):
                extra = {}
            extra["model_accounting"] = meta
            conn.execute("UPDATE ledger SET extra=? WHERE id=?", (json.dumps(extra), row["id"]))
    except sqlite3.Error:
        # Missing telemetry must never prevent returning an already billed response.
        logging.getLogger(__name__).warning("Model accounting metadata could not be recorded")


def gateway_matches(ids: list[str]) -> dict:
    path = os.environ.get("MODEL_GATEWAY_DB", "")
    if not path or not ids:
        return {}
    try:
        with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as conn:
            conn.execute("PRAGMA query_only=ON")
            marks = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"SELECT request_id,MIN(model_name),COUNT(*) FROM logs WHERE type=2 AND request_id IN ({marks}) GROUP BY request_id",
                ids,
            ).fetchall()
        return {r[0]: str(r[1] or "")[:160] for r in rows if r[2] == 1}
    except (sqlite3.Error, OSError, ValueError):
        return {}


def statement(
    cloud: Any,
    account_id: str | None,
    days: int,
    before: int = 0,
    include_provider_cost: bool = False,
) -> dict:
    since = int(cloud.clock()) - days * 86400
    db = cloud.db
    with db._lock:
        conn = db._conn
        columns = {r[1] for r in conn.execute("PRAGMA table_info(ledger)")}
        # Older relays debit cost_uy. Newer relays debit the separate retail sale_uy.
        charge = "sale_uy" if "sale_uy" in columns else "cost_uy"
        where = f"ts>=? AND charged>=0 AND kind IN {KINDS}"
        args: list = [since]
        if account_id is not None:
            where += " AND account_id=?"
            args.append(account_id)
        models = [
            dict(r)
            for r in conn.execute(
                f"SELECT model,kind,COUNT(*) requests,SUM(prompt_tokens) input_tokens,"
                f"SUM(completion_tokens) output_tokens,SUM({charge}) recorded_charge_uy "
                f"FROM ledger WHERE {where} GROUP BY model,kind ORDER BY recorded_charge_uy DESC",
                args,
            )
        ]
        page_where = where + (" AND id<?" if before else "")
        page_args = [*args, before] if before else args
        records = [
            dict(r)
            for r in conn.execute(
                f"SELECT id,ts,kind,model,prompt_tokens,completion_tokens,request_id,extra,"
                f"{charge} recorded_charge_uy FROM ledger WHERE {page_where} ORDER BY id DESC LIMIT 51",
                page_args,
            )
        ]
    for row in models:
        row["recorded_charge_rub"] = _rub(cloud, row.pop("recorded_charge_uy"))
    more = len(records) > 50
    records = records[:50]
    gateway_ids = []
    for row in records:
        row["recorded_charge_rub"] = _rub(cloud, row.pop("recorded_charge_uy"))
        try:
            extra = json.loads(row.pop("extra") or "{}")
        except (ValueError, TypeError):
            extra = {}
        if not isinstance(extra, dict):
            extra = {}
        cached = extra.get("cached_tokens")
        row["cached_tokens"] = (
            min(max(cached, 0), max(row["prompt_tokens"], 0)) if isinstance(cached, int) and not isinstance(cached, bool) else None
        )
        # Do not guess provider, final model, task, channel or actual provider bill.
        meta = extra.get("model_accounting")
        meta = meta if isinstance(meta, dict) else {}
        amount = meta.get("provider_cost_rub")
        row["provider_cost_rub"] = (
            amount
            if include_provider_cost
            and isinstance(amount, (int, float))
            and not isinstance(amount, bool)
            and math.isfinite(amount)
            and amount >= 0
            else None
        )
        upstream = meta.get("upstream_model")
        row["upstream_model"] = upstream[:160] if isinstance(upstream, str) else None
        gateway_id = meta.get("gateway_request_id")
        row["gateway_request_id"] = gateway_id[:160] if isinstance(gateway_id, str) else None
        if row["gateway_request_id"]:
            gateway_ids.append(row["gateway_request_id"])
        supplier = meta.get("provider")
        row["provider"] = supplier[:80] if isinstance(supplier, str) else None
        row["request_id"] = str(row["request_id"] or "")[:160]
    matches = gateway_matches(gateway_ids)
    for row in records:
        row["gateway_model"] = matches.get(row["gateway_request_id"])
    return {
        "days": days,
        "since": since,
        "currency": "RUB",
        "basis": "retail_ledger" if charge == "sale_uy" else "legacy_config_ledger",
        "provider_cost_verified": False,
        "by_model": models,
        "requests": sum(int(r["requests"] or 0) for r in models),
        "recorded_charge_rub": round(sum(r["recorded_charge_rub"] for r in models), 6),
        "records": records,
        "next_before": records[-1]["id"] if records and more else None,
    }


def _rub(cloud: Any, amount: Any) -> float:
    return round(cloud.s.cny_to_rub(cloud.s.uy_to_cny(int(amount or 0))), 6)


def gateway_summary() -> dict:
    """Only aggregate configuration and counts from an optional read-only New API mount."""
    path = os.environ.get("MODEL_GATEWAY_DB", "")
    if not path:
        return {"available": False, "reason": "not_configured"}
    try:
        with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as conn:
            conn.execute("PRAGMA query_only=ON")
            users = conn.execute("SELECT COUNT(*) FROM users WHERE deleted_at IS NULL").fetchone()[0]
            log_users, calls = conn.execute("SELECT COUNT(DISTINCT user_id),COUNT(*) FROM logs").fetchone()
            channels = conn.execute("SELECT COUNT(*) FROM channels WHERE status=1").fetchone()[0]
            model_count = len(
                {
                    m.strip()
                    for (models,) in conn.execute("SELECT models FROM channels WHERE status=1")
                    for m in (models or "").split(",")
                    if m.strip()
                }
            )
        return {
            "available": True,
            "project": "QuantumNous/new-api",
            "users": users,
            "log_users": log_users,
            "log_records": calls,
            "enabled_channels": channels,
            "enabled_models": model_count,
            "customer_identity_mapped": False,
        }
    except (sqlite3.Error, OSError, ValueError):
        return {"available": False, "reason": "unavailable"}


def install(app: Any, cloud: Any, caller_dep: Any, admin_dep: Any) -> None:
    @app.get("/v1/me/model-statement")
    async def mine(
        response: Response,
        days: int = Query(30, ge=1, le=365),
        before: int = Query(0, ge=0),
        caller: Any = Depends(caller_dep),  # noqa: B008
    ) -> dict:
        response.headers["Cache-Control"] = "no-store"
        data = statement(cloud, caller.account_id, days, before)
        data["unlimited_account"] = caller.member or not cloud.limited(caller)
        return data

    @app.get("/v1/admin/model-statement", dependencies=[Depends(admin_dep)])
    async def operator(
        response: Response,
        days: int = Query(30, ge=1, le=365),
        before: int = Query(0, ge=0),
    ) -> dict:
        response.headers["Cache-Control"] = "no-store"
        data = statement(cloud, None, days, before, include_provider_cost=True)
        data["gateway"] = gateway_summary()
        return data
