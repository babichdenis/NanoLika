"""Account isolation, period boundaries and disabled collection regression tests."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from threading import RLock
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI, Header, HTTPException
from httpx import ASGITransport, AsyncClient

from nanomuse_cloud.autopay import Autopay, Preferences
from nanomuse_cloud.autopay import install as install_autopay
from nanomuse_cloud.usage_chart import chart
from nanomuse_cloud.usage_chart import install as install_chart


class DB:
    def __init__(self):
        self._lock = RLock()
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("CREATE TABLE accounts(id TEXT PRIMARY KEY)")
        self._conn.executemany("INSERT INTO accounts VALUES(?)", [("a",), ("b",)])
        self._conn.execute(
            "CREATE TABLE ledger(account_id TEXT,ts INTEGER,model TEXT,prompt_tokens INTEGER,completion_tokens INTEGER,charged INTEGER,kind TEXT,cost_uy INTEGER,sale_uy INTEGER)"
        )

    @contextmanager
    def tx(self):
        with self._lock, self._conn:
            yield self._conn


def fixture():
    now = int(datetime(2026, 10, 25, 15, tzinfo=ZoneInfo("Europe/Berlin")).timestamp())
    return SimpleNamespace(db=DB(), clock=lambda: now, s=SimpleNamespace(cny_to_rub=lambda n: n, uy_to_cny=lambda n: n / 1_000_000))


def add(c, account, ts, model, sale, kind="chat"):
    c.db._conn.execute("INSERT INTO ledger VALUES(?,?,?,?,?,?,?,?,?)", (account, ts, model, 100, 20, 120, kind, 9_000_000, sale))


def test_totals_only_used_models_and_account_isolation():
    c = fixture()
    now = c.clock()
    add(c, "a", now, "used", 1_000_000)
    add(c, "a", now, "free", 0)
    add(c, "b", now, "private", 8_000_000)
    add(c, "a", now + 1, "future", 8_000_000)
    add(c, "a", now - 40 * 86400, "old", 8_000_000)
    result = chart(c, "a", "week", "Europe/Berlin")
    assert [s["model"] for s in result["series"]] == ["used"]
    assert result["total_rub"] == 1 and result["requests"] == 2 and result["tokens"] == 240
    assert result["free_requests"] == 1 and sum(result["series"][0]["values"]) == 1


def test_dst_and_boundaries():
    c = fixture()
    result = chart(c, "a", "week", "Europe/Berlin")
    b = result["buckets"]
    assert len(b) == 7 and b[-1] - b[-2] == 86400
    # End-of-daylight-saving day has a 25-hour elapsed duration by now.
    assert c.clock() - b[-1] == 16 * 3600
    add(c, "a", b[0] - 1, "outside", 1_000_000)
    add(c, "a", b[0], "inside", 2_000_000)
    assert chart(c, "a", "week", "Europe/Berlin")["total_rub"] == 2
    assert len(chart(c, "a", "day", "UTC")["buckets"]) == 24
    assert len(chart(c, "a", "month", "UTC")["buckets"]) == 30
    with pytest.raises(HTTPException):
        chart(c, "a", "week", "../bad")


def test_legacy_column():
    c = fixture()
    c.db._conn.execute("ALTER TABLE ledger DROP COLUMN sale_uy")
    c.db._conn.execute("INSERT INTO ledger VALUES('a',?,'legacy',10,2,12,'chat',1000000)", (c.clock(),))
    r = chart(c, "a", "day", "UTC")
    assert r["total_rub"] == 1 and r["basis"] == "legacy_config_ledger"


def test_autopay_drafts_are_isolated_and_never_enable_debits():
    c = fixture()
    a = Autopay(c)
    p = Preferences(topup_amount_kopecks=30000, threshold_kopecks=5000, monthly_limit_kopecks=90000)
    assert a.save("a", p)["preferences"]["topup_amount_kopecks"] == 30000
    assert a.read("b")["preferences"]["topup_amount_kopecks"] == 59000
    assert a.read("a")["available"] is False and a.read("a")["card"] is None
    for kwargs in [{"renew_plan": True}, {"topup_enabled": True}]:
        with pytest.raises(HTTPException):
            a.save("a", Preferences(**kwargs))
    for kwargs in [{"topup_amount_kopecks": True}, {"threshold_kopecks": -1}, {"monthly_limit_kopecks": 100}, {"card_number": "forbidden"}]:
        with pytest.raises(ValueError):
            Preferences(**kwargs)


async def test_routes_require_auth_and_validate_inputs():
    c = fixture()
    app = FastAPI()

    async def caller(authorization: str = Header(default="")):
        if authorization != "Bearer fixture":
            raise HTTPException(401)
        return SimpleNamespace(account_id="a")

    install_chart(app, c, caller)
    install_autopay(app, c, caller)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://example.test") as client:
        for url in ["/v1/me/usage-chart", "/v1/me/autopay"]:
            assert (await client.get(url)).status_code == 401
        headers = {"Authorization": "Bearer fixture"}
        for url in ["/v1/me/usage-chart?period=bad", "/v1/me/usage-chart?tz=bad"]:
            assert (await client.get(url, headers=headers)).status_code == 422
        r = await client.get("/v1/me/usage-chart", headers=headers)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
        assert (await client.put("/v1/me/autopay", headers=headers, json={"topup_enabled": True})).status_code == 409
