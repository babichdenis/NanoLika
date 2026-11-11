"""Personal model keys must not unlock the account runtime through another door."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from test_accounts import auth, make

from nanomuse_cloud import web_runtime
from nanomuse_cloud.browser_auth import SESSION_COOKIE
from nanomuse_cloud.browser_session_store import browser_store
from nanomuse_cloud.identifiers import parse


def credentials(monkeypatch, tmp_path):
    app, client, _, _, cloud, _ = make()
    session, caller, _ = cloud.sign_in(parse("runtime-scope@example.com"), "browser", "code")
    personal, _ = cloud.create_user_key(caller, "external integration")
    monkeypatch.setattr(web_runtime, "_KEYS", {})
    monkeypatch.setattr(web_runtime, "_KEYS_PATH", str(tmp_path / "keys.json"))
    monkeypatch.setattr(web_runtime, "OWNERS", (caller.account_id,))
    monkeypatch.setattr(web_runtime, "RUNTIME_BASE", "http://127.0.0.1:1")
    return app, client, cloud, session, personal


async def test_personal_key_is_not_a_runtime_or_account_session(monkeypatch, tmp_path):
    app, client, cloud, session, personal = credentials(monkeypatch, tmp_path)
    assert (await client.get("/v1/models", headers=auth(personal))).status_code == 200
    for method, path in (("GET", "/api/threads"), ("DELETE", "/api/cloud/channels/telegram")):
        result = await client.request(method, path, headers=auth(personal))
        assert result.status_code == 401, (path, result.status_code, result.text)
    # A model key cannot acquire runtime privileges by being wrapped in a session cookie.
    token, _ = browser_store(cloud).issue(cloud.authenticate(personal))
    result = await client.get("/api/threads", headers={"cookie": f"{SESSION_COOKIE}={token}"})
    assert result.status_code == 401
    browser_token, _ = browser_store(cloud).issue(cloud.authenticate(session))
    assert (await client.get("/api/threads", headers={"cookie": f"{SESSION_COOKIE}={browser_token}"})).status_code == 502
    # Normal sessions still reach the worker (which is deliberately absent in this test).
    assert (await client.get("/api/threads", headers=auth(session))).status_code == 502
    await client.aclose()


def test_personal_key_is_rejected_in_websocket_header_and_first_frame(monkeypatch, tmp_path):
    app, client, cloud, session, personal = credentials(monkeypatch, tmp_path)
    called = []

    async def unavailable(*args, **kwargs):
        called.append(True)
        raise OSError("test worker absent")

    monkeypatch.setattr(web_runtime.websockets, "connect", unavailable)
    with TestClient(app) as http:
        with pytest.raises(WebSocketDisconnect) as closed:
            with http.websocket_connect("/ws", headers=auth(personal)) as ws:
                ws.receive_json()
        assert closed.value.code == 4401
        with pytest.raises(WebSocketDisconnect) as closed:
            with http.websocket_connect("/ws") as ws:
                ws.send_json({"kind": "auth", "token": personal})
                ws.receive_json()
        assert closed.value.code == 4401
        token, _ = browser_store(cloud).issue(cloud.authenticate(personal))
        with pytest.raises(WebSocketDisconnect) as closed:
            with http.websocket_connect("/ws", headers={"cookie": f"{SESSION_COOKIE}={token}"}) as ws:
                ws.receive_json()
        assert closed.value.code == 4401
        assert not called
        with pytest.raises(WebSocketDisconnect) as closed:
            with http.websocket_connect("/ws", headers=auth(session)) as ws:
                ws.receive_json()
        assert closed.value.code == 4502
        assert called == [True]
