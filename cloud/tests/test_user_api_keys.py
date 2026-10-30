"""Personal model API keys are scoped, accounted, visible, and revocable."""

from __future__ import annotations

import hashlib
import time

from test_accounts import auth, make
from test_cloud import sign_up
from nanomuse_cloud.config import ModelSpec


async def test_personal_key_can_infer_but_cannot_manage_the_account():
    app, client, sender, upstream, cloud, settings = make()
    signed_in = await sign_up(client, sender, "api-key@example.com", "browser")
    session_headers = auth(signed_in["api_key"])

    created = await client.post("/v1/me/keys", json={"device": "test integration"}, headers=session_headers)
    assert created.status_code == 200, created.text
    key = created.json()["api_key"]
    key_headers = auth(key)

    models = await client.get("/v1/models", headers=key_headers)
    assert models.status_code == 200, models.text

    chat = await client.post(
        "/v1/chat/completions",
        json={"model": "qwen3.8-27b", "messages": [{"role": "user", "content": "hello"}]},
        headers=key_headers,
    )
    assert chat.status_code == 200, chat.text
    usage = cloud.db._conn.execute(
        "SELECT key_hash FROM ledger WHERE account_id=? AND kind='chat' ORDER BY ts DESC LIMIT 1",
        (signed_in["account"]["id"],),
    ).fetchone()
    assert usage["key_hash"] == hashlib.sha256(key.encode()).hexdigest()

    for method, path in (
        ("GET", "/v1/me"),
        ("GET", "/v1/me/keys"),
        ("GET", "/v1/devices"),
        ("POST", "/v1/auth/sign-out-all"),
    ):
        response = await client.request(method, path, headers=key_headers, json={})
        assert response.status_code == 403, (method, path, response.text)
        assert response.json()["error"]["code"] == "api_key_scope"

    listed = await client.get("/v1/me/keys", headers=session_headers)
    assert listed.status_code == 200
    personal = next(item for item in listed.json()["keys"] if item["via"] == "user")
    assert personal["prefix"] == created.json()["prefix"]
    assert personal["total_sale_rub"] >= 0

    sessions = await client.get("/v1/me/sessions", headers=session_headers)
    assert all(item["via"] != "user" for item in sessions.json()["sessions"])

    revoked = await client.delete(f"/v1/me/keys/{created.json()['prefix']}", headers=session_headers)
    assert revoked.status_code == 204
    after_revoke = await client.get("/v1/models", headers=key_headers)
    assert after_revoke.status_code == 401
    await client.aclose()


async def test_key_lifetime_limits_and_edit():
    app, client, sender, upstream, cloud, settings = make()
    signed_in = await sign_up(client, sender, "key-meta@example.com", "browser")
    session_headers = auth(signed_in["api_key"])

    created = await client.post(
        "/v1/me/keys",
        json={
            "device": "integration",
            "label": "Мой бот",
            "ttl_s": 3600,
            "daily_limit_rub": 100,
            "monthly_limit_rub": 500,
        },
        headers=session_headers,
    )
    assert created.status_code == 200, created.text
    key = created.json()["api_key"]
    key_headers = auth(key)

    own = await client.get("/v1/me/key", headers=key_headers)
    assert own.status_code == 200, own.text
    view = own.json()
    assert view["label"] == "Мой бот"
    assert view["daily_limit_rub"] == 100
    assert view["monthly_limit_rub"] == 500
    assert view["expires_at"] and time.time() + 3000 < view["expires_at"] < time.time() + 3700
    assert view["balance"]["unlimited"] is False

    listed = await client.get("/v1/me/keys", headers=session_headers)
    item = next(row for row in listed.json()["keys"] if row["prefix"] == created.json()["prefix"])
    assert item["label"] == "Мой бот"
    assert item["daily_limit_rub"] == 100
    assert item["monthly_limit_rub"] == 500
    assert item["spent_today_rub"] >= 0

    edited = await client.patch(
        f"/v1/me/keys/{created.json()['prefix']}",
        json={"label": "Бот 2", "daily_limit_rub": 0, "monthly_limit_rub": 300, "ttl_s": 0},
        headers=session_headers,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["key"]["label"] == "Бот 2"
    assert edited.json()["key"]["daily_limit_rub"] == 0
    assert edited.json()["key"]["monthly_limit_rub"] == 300
    assert edited.json()["key"]["expires_at"] is None

    bad = await client.patch(
        f"/v1/me/keys/{created.json()['prefix']}",
        json={"daily_limit_rub": -1},
        headers=session_headers,
    )
    assert bad.status_code == 400
    await client.aclose()


async def test_key_daily_limit_stops_inference_until_raised():
    priced = ModelSpec(
        id="qwen3.8-27b",
        name="Qwen 3.8",
        upstream="qwen3.8-27b",
        sell_in=1000.0,
        sell_out=1000.0,
    )
    app, client, sender, upstream, cloud, settings = make(models=(priced,), rub_per_usd=90.0, usd_cny=7.2)
    signed_in = await sign_up(client, sender, "key-limit@example.com", "browser")
    session_headers = auth(signed_in["api_key"])

    created = await client.post(
        "/v1/me/keys",
        json={"device": "integration", "label": "Лимит", "daily_limit_rub": 1},
        headers=session_headers,
    )
    assert created.status_code == 200, created.text
    key_headers = auth(created.json()["api_key"])
    prefix = created.json()["prefix"]

    blocked = await client.post(
        "/v1/chat/completions",
        json={"model": "qwen3.8-27b", "messages": [{"role": "user", "content": "hello"}]},
        headers=key_headers,
    )
    assert blocked.status_code == 429, blocked.text
    assert blocked.json()["error"]["code"] == "key_limit"

    raised = await client.patch(
        f"/v1/me/keys/{prefix}",
        json={"daily_limit_rub": 0, "monthly_limit_rub": 0},
        headers=session_headers,
    )
    assert raised.status_code == 200, raised.text

    allowed = await client.post(
        "/v1/chat/completions",
        json={"model": "qwen3.8-27b", "messages": [{"role": "user", "content": "hello"}]},
        headers=key_headers,
    )
    assert allowed.status_code == 200, allowed.text
    await client.aclose()
