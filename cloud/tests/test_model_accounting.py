from __future__ import annotations

import json
import sqlite3
import threading
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from nanomuse_cloud.model_accounting import gateway_summary, install, statement


def cloud(retail=False):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE ledger(id INTEGER PRIMARY KEY,account_id TEXT,ts INT,kind TEXT,model TEXT,prompt_tokens INT,completion_tokens INT,charged INT,request_id TEXT,cost_uy INT,extra TEXT"
        + (",sale_uy INT" if retail else "")
        + ")"
    )
    s = SimpleNamespace(uy_to_cny=lambda x: x / 1_000_000, cny_to_rub=lambda x: x * 10)
    return SimpleNamespace(db=SimpleNamespace(_conn=conn, _lock=threading.RLock()), s=s, clock=lambda: 1_000_000, limited=lambda _: True)


def put(c, account="a", model="model-a", extra=None, kind="chat", sale=None):
    vals = [account, 999_999, kind, model, 100, 20, 120, "req", 100_000, json.dumps(extra or {})]
    cols = "account_id,ts,kind,model,prompt_tokens,completion_tokens,charged,request_id,cost_uy,extra"
    if sale is not None:
        cols += ",sale_uy"
        vals.append(sale)
    c.db._conn.execute(f"INSERT INTO ledger({cols}) VALUES({','.join('?' for _ in vals)})", vals)


def test_isolation_and_no_content():
    c = cloud()
    put(c, extra={"cached_tokens": 40, "prompt": "SECRET", "key": "KEY"})
    put(c, "b", "other")
    put(c, kind="credit")
    data = statement(c, "a", 30)
    assert data["requests"] == 1 and len(data["records"]) == 1
    assert data["records"][0]["cached_tokens"] == 40
    assert data["records"][0]["provider_cost_rub"] is None
    assert "SECRET" not in json.dumps(data) and "KEY" not in json.dumps(data)
    assert data["recorded_charge_rub"] == 1


def test_retail_and_historical_basis():
    c = cloud(True)
    put(c, sale=300_000)
    data = statement(c, "a", 30)
    assert data["basis"] == "retail_ledger" and data["recorded_charge_rub"] == 3
    assert statement(cloud(), "a", 30)["basis"] == "legacy_config_ledger"


def test_pagination_and_malformed_extra():
    c = cloud()
    for _ in range(55):
        put(c)
    c.db._conn.execute("UPDATE ledger SET extra='not-json' WHERE id=55")
    first = statement(c, "a", 30)
    second = statement(c, "a", 30, first["next_before"])
    assert len(first["records"]) == 50 and len(second["records"]) == 5
    assert not ({r["id"] for r in first["records"]} & {r["id"] for r in second["records"]})
    assert second["next_before"] is None
    assert first["records"][0]["cached_tokens"] is None


def test_routes_require_auth_and_validate():
    c = cloud()
    put(c)
    from fastapi import Header

    def caller(authorization: str = Header("")):
        if authorization != "Bearer a":
            raise HTTPException(401)
        return SimpleNamespace(account_id="a", member=False)

    def admin(x_admin_token: str = Header("")):
        if x_admin_token != "admin":
            raise HTTPException(401)

    app = FastAPI()
    install(app, c, caller, admin)
    with TestClient(app) as client:
        assert client.get("/v1/me/model-statement").status_code == 401
        assert client.get("/v1/admin/model-statement").status_code == 401
        result = client.get("/v1/me/model-statement?account_id=b", headers={"Authorization": "Bearer a"})
        assert result.status_code == 200 and result.json()["requests"] == 1
        assert result.headers["cache-control"] == "no-store"
        assert client.get("/v1/me/model-statement?days=366", headers={"Authorization": "Bearer a"}).status_code == 422


def test_gateway_read_only_and_no_secrets(tmp_path, monkeypatch):
    path = tmp_path / "gateway.db"
    c = sqlite3.connect(path)
    c.executescript(
        "CREATE TABLE users(id INT,deleted_at TEXT,password TEXT);CREATE TABLE channels(status INT,models TEXT,key TEXT);CREATE TABLE logs(user_id INT,content TEXT);"
    )
    c.execute("INSERT INTO users VALUES(1,NULL,'SECRET')")
    c.execute("INSERT INTO channels VALUES(1,'a,b,a','KEY')")
    c.execute("INSERT INTO logs VALUES(1,'CONTENT')")
    c.commit()
    c.close()
    monkeypatch.setenv("MODEL_GATEWAY_DB", str(path))
    r = gateway_summary()
    assert r["available"] and r["enabled_models"] == 2 and r["log_users"] == 1
    assert r["customer_identity_mapped"] is False
    assert all(s not in json.dumps(r) for s in ["SECRET", "KEY", "CONTENT"])
    monkeypatch.setenv("MODEL_GATEWAY_DB", str(tmp_path / "missing"))
    assert not gateway_summary()["available"]
    assert not (tmp_path / "missing").exists()


def test_capture_cost_validation_and_gateway_id():
    from nanomuse_cloud.model_accounting import capture_metadata

    for value in [True, "-1", "NaN", "Infinity", {}, "bad"]:
        assert "provider_cost_rub" not in capture_metadata({}, {"usage": {"cost_rub": value}})
    assert capture_metadata({"x-oneapi-request-id": "gateway-id"}, {"model": "resolved", "usage": {"cost_rub": "0.00115"}}) == {
        "gateway_request_id": "gateway-id",
        "upstream_model": "resolved",
        "provider_cost_rub": 0.00115,
    }


def test_metadata_joins_only_exact_gateway_id_and_hides_wholesale(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from nanomuse_cloud.model_accounting import record_metadata

    c = cloud()
    put(c)

    @contextmanager
    def tx():
        with c.db._lock:
            yield c.db._conn

    c.db.tx = tx
    record_metadata(c, "b", "req", {"provider_cost_rub": 999})
    assert statement(c, "a", 30)["records"][0]["provider_cost_rub"] is None
    record_metadata(c, "a", "req", {"gateway_request_id": "g1", "upstream_model": "upstream", "provider_cost_rub": 0.01})
    p = tmp_path / "gateway.db"
    db = sqlite3.connect(p)
    db.execute("CREATE TABLE logs(request_id TEXT,model_name TEXT,type INT)")
    db.execute("INSERT INTO logs VALUES('g1','resolved-model',2)")
    db.execute("INSERT INTO logs VALUES('g2','other-account',2)")
    db.commit()
    db.close()
    monkeypatch.setenv("MODEL_GATEWAY_DB", str(p))
    customer = statement(c, "a", 30)["records"][0]
    operator = statement(c, None, 30, include_provider_cost=True)["records"][0]
    assert customer["gateway_model"] == "resolved-model" and customer["provider_cost_rub"] is None
    assert operator["provider_cost_rub"] == 0.01
