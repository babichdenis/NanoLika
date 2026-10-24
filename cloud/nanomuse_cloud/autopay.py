"""Account-owned autopay preferences. No bank credentials or collection worker.

Saving a draft is not a debit mandate. Enabling collection is deliberately rejected
until recurring checkout, revocation, receipt delivery and idempotent jobs are released.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    topup_amount_kopecks: int = Field(default=59000, ge=100, le=10_000_000)
    threshold_kopecks: int = Field(default=10000, ge=0, le=10_000_000)
    monthly_limit_kopecks: int = Field(default=177000, ge=100, le=10_000_000)
    renew_plan: bool = False
    topup_enabled: bool = False

    @model_validator(mode="after")
    def valid_limit(self) -> Preferences:
        if self.monthly_limit_kopecks < self.topup_amount_kopecks:
            raise ValueError("Monthly limit must cover one top-up")
        return self


class Autopay:
    def __init__(self, cloud: Any):
        self.cloud = cloud
        with cloud.db.tx() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS autopay_preferences (
                account_id TEXT PRIMARY KEY REFERENCES accounts(id),
                topup_amount_kopecks INTEGER NOT NULL,
                threshold_kopecks INTEGER NOT NULL,
                monthly_limit_kopecks INTEGER NOT NULL,
                updated_at INTEGER NOT NULL)""")

    def read(self, account_id: str) -> dict:
        with self.cloud.db._lock:
            r = self.cloud.db._conn.execute(
                "SELECT topup_amount_kopecks,threshold_kopecks,monthly_limit_kopecks,updated_at "
                "FROM autopay_preferences WHERE account_id=?",
                (account_id,),
            ).fetchone()
        return {
            "available": False,
            "reason": "recurring_checkout_not_enabled",
            "card": None,
            "preferences": {**Preferences().model_dump(), **(dict(r) if r else {})},
        }

    def save(self, account_id: str, body: Preferences) -> dict:
        if body.renew_plan or body.topup_enabled:
            raise HTTPException(409, "Automatic collection is not enabled")
        with self.cloud.db.tx() as c:
            c.execute(
                "INSERT INTO autopay_preferences VALUES(?,?,?,?,?) "
                "ON CONFLICT(account_id) DO UPDATE SET "
                "topup_amount_kopecks=excluded.topup_amount_kopecks,"
                "threshold_kopecks=excluded.threshold_kopecks,"
                "monthly_limit_kopecks=excluded.monthly_limit_kopecks,"
                "updated_at=excluded.updated_at",
                (account_id, body.topup_amount_kopecks, body.threshold_kopecks, body.monthly_limit_kopecks, int(self.cloud.clock())),
            )
        return self.read(account_id)


def install(app: Any, cloud: Any, caller_dep: Any) -> None:
    service = Autopay(cloud)

    @app.get("/v1/me/autopay")
    async def read(response: Response, caller: Any = Depends(caller_dep)) -> dict:  # noqa: B008
        response.headers["Cache-Control"] = "no-store"
        return service.read(caller.account_id)

    @app.put("/v1/me/autopay")
    async def save(body: Preferences, response: Response, caller: Any = Depends(caller_dep)) -> dict:  # noqa: B008
        response.headers["Cache-Control"] = "no-store"
        return service.save(caller.account_id, body)
