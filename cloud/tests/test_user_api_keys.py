"""Personal model API keys are scoped, accounted, visible, and revocable."""

from __future__ import annotations

import hashlib

from test_accounts import auth, make
from test_cloud import sign_up


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
