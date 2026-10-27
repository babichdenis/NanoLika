"""Bounded bank reconciliation. No new charge, refund or subscription purchase."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from .service import CloudError

if TYPE_CHECKING:
    from fastapi import FastAPI

    from .payments import Payments


async def reconcile_batch(payments: Payments, *, at: int | None = None, limit: int = 20) -> dict:
    payments.ready()
    at = int(time.time()) if at is None else at
    limit = max(1, min(limit, 100))
    with payments.db.tx() as c:
        rows = c.execute(
            "SELECT * FROM tbank_orders WHERE terminal=? AND checked_at<=? AND created_at>=? "
            "AND status NOT IN ('REFUNDED','REVERSED','CANCELED','REJECTED','AUTH_FAIL','DEADLINE_EXPIRED') "
            "ORDER BY checked_at,created_at,id LIMIT ?",
            (payments.terminal, at - 60, at - 90 * 86400, limit),
        ).fetchall()
    result = {"checked": 0, "failed": 0, "recovered": 0}
    for selected in rows:
        with payments.db.tx() as c:
            claimed = c.execute(
                "UPDATE tbank_orders SET checked_at=? WHERE id=? AND checked_at=?", (at, selected["id"], selected["checked_at"])
            ).rowcount
        if not claimed:
            continue
        try:
            row = dict(selected)
            if not row["payment_id"]:
                lookup = await payments.bank("CheckOrder", {"OrderId": row["id"]})
                matches = lookup.get("Payments")
                if (
                    lookup.get("TerminalKey") != payments.terminal
                    or lookup.get("OrderId") != row["id"]
                    or not isinstance(matches, list)
                    or len(matches) != 1
                ):
                    raise ValueError("payment_lookup_ambiguous")
                item = matches[0]
                pid = str(item.get("PaymentId", ""))
                if not payments.valid_payment_id(pid) or type(item.get("Amount")) is not int or not 0 <= item["Amount"] <= row["amount"]:
                    raise ValueError("payment_lookup_mismatch")
                # Always validate against independent GetState before storing a recovered ID.
                row["payment_id"] = pid
                result["recovered"] += 1
            state = await payments.bank("GetState", {"PaymentId": row["payment_id"]})
            payments.apply(state, reconciled=True)
            # NEW/pending states do not credit; they still need their ID retained for recovery.
            with payments.db.tx() as c:
                c.execute("UPDATE tbank_orders SET payment_id=? WHERE id=? AND payment_id IS NULL", (row["payment_id"], row["id"]))
            result["checked"] += 1
        except (CloudError, ValueError, TypeError, KeyError):
            # Never log a request, provider body, credentials or recipient.
            result["failed"] += 1
    return result


def install_payment_jobs(app: FastAPI, payments: Payments) -> None:
    original = app.router.lifespan_context
    payments.jobs_state = {"running": False, "last_run": 0, "last_result": {}}

    async def run() -> None:
        while True:
            await asyncio.sleep(60)
            try:
                result = await reconcile_batch(payments)
                payments.jobs_state.update(last_run=int(time.time()), last_result=result)
            except CloudError:
                payments.jobs_state.update(last_run=int(time.time()), last_result={"blocked": True})
            except Exception:
                # A failed round must not kill future reconciliation or leak provider details.
                payments.jobs_state.update(last_run=int(time.time()), last_result={"failed": True})

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        async with original(application):
            task = asyncio.create_task(run(), name="payment-reconciliation")
            payments.jobs_state["running"] = True
            try:
                yield
            finally:
                payments.jobs_state["running"] = False
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    app.router.lifespan_context = lifespan
