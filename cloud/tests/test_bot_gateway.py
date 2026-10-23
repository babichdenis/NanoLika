"""Gateway checks use fake provider HTTP and an isolated SQLite account database."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from nanomuse_cloud.bot_gateway import digest, install
from nanomuse_cloud.db import Database
from nanomuse_cloud.service import CloudError

TOKEN = "123456:" + "a" * 36
MAX_TOKEN = "m" * 48


@pytest.fixture
def stack():
    db = Database(":memory:")
    for account in ("owner", "other"):
        with db.tx() as c:
            c.execute(
                "INSERT INTO accounts(id,id_hash,channel,hint,created_at) VALUES(?,?,?,?,?)", (account, account, "email", "fixture", 1)
            )
    plan = {"active": True, "connect": True, "price_rub": 1490}
    cloud = SimpleNamespace(db=db, s=SimpleNamespace(public_base="https://cloud.test"), plan_state=lambda _: dict(plan))

    def auth(key):
        if key not in ("owner-key", "other-key"):
            raise CloudError(401, "bad_key", "Sign in")
        return SimpleNamespace(account_id="owner" if key == "owner-key" else "other")

    cloud.authenticate = auth
    calls = []
    secrets = {}

    def provider(request):
        import json

        calls.append(request)
        body = json.loads(request.content) if request.content and request.content != b"null" else {}
        path = request.url.path
        if path.endswith("getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"id": 123456, "is_bot": True, "username": "fixture_bot"}})
        if path == "/me":
            return httpx.Response(200, json={"user_id": 123456, "is_bot": True, "username": "fixture_bot"})
        if path.endswith("getWebhookInfo"):
            return httpx.Response(200, json={"ok": True, "result": {"url": ""}})
        if path == "/subscriptions" and request.method == "GET":
            return httpx.Response(200, json={"subscriptions": []})
        if path.endswith("setWebhook") or path == "/subscriptions":
            secrets["secret"] = body.get("secret_token") or body.get("secret")
            secrets["url"] = body["url"]
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}, "success": True})

    app = FastAPI()

    @app.exception_handler(CloudError)
    async def error(request, exc):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    def caller(authorization: str | None = Header(default=None)):
        return auth((authorization or "").removeprefix("Bearer "))

    gateway = install(app, cloud, caller, httpx.MockTransport(provider))
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer owner-key"
        yield client, cloud, plan, calls, secrets, gateway
    db.close()


def register(stack, platform="telegram"):
    client, *_ = stack
    response = client.post(
        "/v1/bot-projects", json={"platform": platform, "label": "Test bot", "token": TOKEN if platform == "telegram" else MAX_TOKEN}
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_registration_persists_only_metadata_and_fingerprints(stack):
    client, cloud, _, calls, secrets, _ = stack
    pid = register(stack)
    row = cloud.db.bot_project(pid)
    assert row["token_hash"] == digest(TOKEN)
    assert TOKEN not in str(row)
    assert secrets["secret"] not in str(row)
    assert "payload" not in row and "code" not in row
    assert secrets["url"] == "https://cloud.test/v1/bot-projects/hooks/" + pid
    assert client.get("/v1/bot-projects").json()["storage"] == "owner_device"


@pytest.mark.parametrize("change", [{"active": False}, {"connect": False}, {"price_rub": 0}])
def test_no_paid_connect_plan_no_provider_calls(stack, change):
    client, _, plan, calls, _, _ = stack
    plan.update(change)
    response = client.post("/v1/bot-projects", json={"platform": "telegram", "token": TOKEN})
    assert response.status_code == 403
    assert calls == []


def test_expiry_rechecked_before_each_call_and_no_token_mixing(stack):
    client, _, plan, calls, _, _ = stack
    pid = register(stack)
    path = "/v1/bot-projects/" + pid + "/call"
    payload = {"token": TOKEN, "method": "sendMessage", "body": {"chat_id": 1, "text": "fixture"}}
    assert client.post(path, json=payload).status_code == 200
    assert client.post(path, json={**payload, "token": "999999:" + "b" * 36}).status_code == 403
    before = len(calls)
    plan["active"] = False
    assert client.post(path, json=payload).status_code == 403
    assert len(calls) == before


def test_other_account_cannot_call_pause_or_connect_worker(stack):
    client, *_ = stack
    pid = register(stack)
    headers = {"Authorization": "Bearer other-key"}
    assert (
        client.post("/v1/bot-projects/" + pid + "/call", headers=headers, json={"token": TOKEN, "method": "sendMessage"}).status_code == 404
    )
    assert client.post("/v1/bot-projects/" + pid + "/state", headers=headers, json={"enabled": False}).status_code == 404
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/v1/bot-projects/" + pid + "/socket", headers=headers) as ws:
            ws.receive_json()


def test_route_changes_and_arbitrary_urls_blocked(stack):
    client, *_ = stack
    pid = register(stack)
    for method in ("setWebhook", "deleteWebhook", "getUpdates", "../../file", "https://evil.test"):
        assert client.post("/v1/bot-projects/" + pid + "/call", json={"token": TOKEN, "method": method}).status_code >= 400


def test_webhook_requires_secret_and_offline_returns_retry(stack):
    client, _, _, _, secrets, _ = stack
    pid = register(stack)
    path = "/v1/bot-projects/hooks/" + pid
    assert client.post(path, json={"update_id": 1}).status_code == 403
    assert client.post(path, headers={"X-Telegram-Bot-Api-Secret-Token": secrets["secret"]}, json={"update_id": 1}).status_code == 503


def test_webhook_ack_only_after_device_durable_receipt(stack):
    client, cloud, _, _, secrets, gateway = stack
    pid = register(stack)
    with client.websocket_connect("/v1/bot-projects/" + pid + "/socket", headers={"Authorization": "Bearer owner-key"}) as ws:
        assert ws.receive_json()["type"] == "ready"
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                client.post,
                "/v1/bot-projects/hooks/" + pid,
                headers={"X-Telegram-Bot-Api-Secret-Token": secrets["secret"]},
                json={"update_id": 1, "message": {"text": "local only"}},
            )
            frame = ws.receive_json()
            assert frame["update"]["message"]["text"] == "local only"
            assert not future.done()
            ws.send_json({"type": "ack", "delivery": frame["delivery"], "stored": True})
            assert future.result(timeout=5).status_code == 200
        assert gateway.workers[pid].pending == {}
        assert "local only" not in str(cloud.db.bot_project(pid))


def test_pause_keeps_project_and_expired_hooks_do_not_queue(stack):
    client, cloud, plan, _, secrets, _ = stack
    pid = register(stack)
    plan["active"] = False
    path = "/v1/bot-projects/hooks/" + pid
    response = client.post(
        path, headers={"X-Telegram-Bot-Api-Secret-Token": secrets["secret"]}, json={"update_id": 2, "message": {"text": "not stored"}}
    )
    assert response.json() == {"ok": True, "paused": True}
    assert cloud.db.bot_project(pid) is not None
    assert client.post("/v1/bot-projects/" + pid + "/state", json={"enabled": False}).status_code == 200
    assert client.post("/v1/bot-projects/" + pid + "/state", json={"enabled": True}).status_code == 403


def test_max_uses_verified_api_and_scoped_provider_paths(stack):
    client, _, _, calls, secrets, _ = stack
    pid = register(stack, "max")
    assert any(r.url.host == "platform-api2.max.ru" for r in calls)
    response = client.post(
        "/v1/bot-projects/" + pid + "/call",
        json={"token": MAX_TOKEN, "method": "/messages", "params": {"chat_id": 1}, "body": {"text": "hello"}},
    )
    assert response.status_code == 200
    assert client.post("/v1/bot-projects/" + pid + "/call", json={"token": MAX_TOKEN, "method": "/subscriptions"}).status_code == 403


def test_older_relay_does_not_treat_demo_wallet_as_paid_subscription(stack):
    client, cloud, _, _, _, gateway = stack
    del cloud.plan_state
    with cloud.db.tx() as c:
        c.execute("CREATE TABLE tbank_demo_wallets(account_id TEXT PRIMARY KEY,balance INTEGER,plan TEXT,expires_at INTEGER)")
        c.execute("INSERT INTO tbank_demo_wallets VALUES('owner',999999,'plus',9999999999)")
    assert gateway.plan("owner") is None
    assert client.get("/v1/bot-projects").json()["gateway_allowed"] is False
    assert client.post("/v1/bot-projects", json={"platform": "telegram", "token": TOKEN}).status_code == 403


def test_older_relay_paid_subscription_is_checked_against_expiry(stack):
    import time

    client, cloud, _, _, _, gateway = stack
    del cloud.plan_state
    with cloud.db.tx() as c:
        c.execute(
            "INSERT INTO subscriptions(account_id,plan,name,price_rub,connect,started_at,expires_at) VALUES('owner','plus','Лика+',1490,1,1,?)",
            (int(time.time()) + 3600,),
        )
    assert client.get("/v1/bot-projects").json()["gateway_allowed"] is True
    with cloud.db.tx() as c:
        c.execute("UPDATE subscriptions SET expires_at=1 WHERE account_id='owner'")
    assert client.get("/v1/bot-projects").json()["gateway_allowed"] is False


def test_refresh_reinstalls_webhook_without_storing_raw_secret(stack):
    client, cloud, _, calls, secrets, _ = stack
    pid = register(stack)
    first = cloud.db.bot_project(pid)["secret_hash"]
    response = client.post("/v1/bot-projects/" + pid + "/refresh", json={"token": TOKEN})
    assert response.status_code == 200
    row = cloud.db.bot_project(pid)
    assert row["secret_hash"] != first
    assert row["secret_hash"] == digest(secrets["secret"])
    assert secrets["secret"] not in str(row)


def test_disconnect_is_possible_after_expiry_and_keeps_local_files(stack):
    client, cloud, plan, _, _, _ = stack
    pid = register(stack)
    plan["active"] = False
    response = client.post("/v1/bot-projects/" + pid + "/disconnect", json={"token": TOKEN})
    assert response.status_code == 200
    assert response.json()["local_files_retained"] is True
    assert cloud.db.bot_project(pid) is None


def test_telegram_token_masked_in_httpx_logs():
    import logging

    from nanomuse_cloud.bot_gateway import BotCredentialFilter

    record = logging.LogRecord(
        "httpx", logging.INFO, "fixture", 1, "HTTP request %s", ("https://api.telegram.org/bot" + TOKEN + "/getMe",), None
    )
    assert BotCredentialFilter().filter(record)
    assert TOKEN not in record.getMessage()
    assert "[redacted]" in record.getMessage()
