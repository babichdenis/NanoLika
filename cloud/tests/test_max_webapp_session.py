import asyncio
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from nanomuse_cloud.channel_links import (
    _max_session,
    _validate_max_init_data,
    install,
)
from nanomuse_cloud.config import Settings
from nanomuse_cloud.db import Database
from nanomuse_cloud.identifiers import parse
from nanomuse_cloud.senders import LogSender
from nanomuse_cloud.service import Cloud, CloudError

TOKEN = "fixture-only-max-token"


def max_hash(data: dict[str, str]) -> str:
    """The signature MAX describes: HMAC keyed by 'WebAppData' over the token, then over
    the newline-joined launch parameters."""
    launch = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    return hmac.new(secret, launch.encode(), hashlib.sha256).hexdigest()


def signed(**updates):
    data = {
        "auth_date": str(int(time.time())),
        "query_id": "fixture+query",
        "user": json.dumps(
            {"id": 654321, "first_name": "Лика + & =", "username": "fixture"},
            ensure_ascii=False,
        ),
        "chat": json.dumps({"id": 490862977, "type": "DIALOG"}),
    }
    data.update(updates)
    data["hash"] = max_hash(data)
    return urlencode(data)


def cloud_fixture(link=True):
    c = Cloud(
        Settings(
            database=":memory:",
            secret="fixture-secret",
            max_bot_token=TOKEN,
            public_base="https://cloud.example.test",
        ),
        Database(":memory:"),
        LogSender(),
    )
    key, caller, _ = c.sign_in(parse("owner@example.test"), "browser", "code")
    if link:
        code, _ = c.channel_code(caller)
        c.link_channel("max", "654321", code, "fixture-max")
    return c, caller, key


def test_decodes_unicode_and_signature():
    assert _validate_max_init_data(signed(), TOKEN)["user"]["first_name"] == "Лика + & ="


@pytest.mark.parametrize(
    "raw",
    [
        signed(auth_date=str(int(time.time()) - 301)),
        signed(auth_date=str(int(time.time()) + 3600)),
        signed(user='{"id":true}'),
        signed(user='{"id":-1}'),
        signed(user="[]"),
        signed(user='{"id":4503599627370496}'),
        signed().replace("fixture%2Bquery", "tampered"),
        signed() + "&hash=duplicate",
        signed() + "&broken=%ZZ",
        signed() + "&user=%FF",
        "",
    ],
)
def test_rejects_bad_launch(raw):
    assert _validate_max_init_data(raw, TOKEN) is None


def test_bound_account_session():
    c, caller, oldkey = cloud_fixture()
    result = _max_session(c, 654321)
    new = c.authenticate(result["api_key"])
    assert new.account_id == caller.account_id
    assert new.via == "max_webapp"
    assert c.authenticate(oldkey).account_id == caller.account_id
    assert result["account"]["hint"] == caller.hint
    keys = c.db.keys_for(caller.account_id, live_only=True)
    web = [k for k in keys if k["via"] == "max_webapp"][0]
    assert 29 * 86400 < web["expires_at"] - time.time() <= 30 * 86400


def test_unlinked_does_not_register():
    c, caller, _ = cloud_fixture(False)
    with pytest.raises(CloudError) as error:
        _max_session(c, 654321)
    assert error.value.code == "max_not_linked"
    assert len(c.db.keys_for(caller.account_id, live_only=True)) == 1
    assert not c.chat_link("max", "654321")["linked"]


def test_unlinked_again_refused():
    c, caller, _ = cloud_fixture()
    c.unlink_channel(caller, "max")
    with pytest.raises(CloudError):
        _max_session(c, 654321)


def test_disabled_refused():
    c, caller, _ = cloud_fixture()
    with c.db.tx() as conn:
        conn.execute("UPDATE accounts SET disabled=1 WHERE id=?", (caller.account_id,))
    with pytest.raises(CloudError) as error:
        _max_session(c, 654321)
    assert error.value.status == 403


@pytest.mark.parametrize(
    "origin,content,raw_kind,expected",
    [
        ("https://cloud.example.test", "application/json", "valid", 200),
        ("https://evil.example", "application/json", "valid", 403),
        ("", "application/json", "valid", 403),
        ("https://cloud.example.test", "text/plain", "valid", 415),
        ("https://cloud.example.test", "application/json", "invalid", 401),
        ("https://cloud.example.test", "application/json", "other", 409),
    ],
)
def test_endpoint(origin, content, raw_kind, expected):
    raw = "invalid" if raw_kind == "invalid" else signed(**({"user": '{"id":111222}'} if raw_kind == "other" else {}))
    c, caller, _ = cloud_fixture()
    app = FastAPI()

    @app.exception_handler(CloudError)
    async def err(request, e):
        return JSONResponse({"error": e.code}, status_code=e.status)

    install(app, c, lambda: caller, lambda r: None)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://cloud.example.test",
        ) as client:
            response = await client.post(
                "/v1/auth/max-webapp",
                headers={"origin": origin, "content-type": content},
                json={"init_data": raw},
            )
            assert response.status_code == expected, response.text
            if expected == 200:
                assert response.json()["account"]["id"] == caller.account_id
                assert response.headers["cache-control"] == "private, no-store"
                cookies = ";".join(response.headers.get_list("set-cookie"))
                assert "__Host-nanolika_session=" in cookies and "HttpOnly" in cookies and "Secure" in cookies
            else:
                assert "set-cookie" not in response.headers

    asyncio.run(run())


def test_max_and_telegram_are_separate_accounts():
    from nanomuse_cloud.service import chat_identity

    c, _, _ = cloud_fixture(False)
    _, telegram_owner, _ = c.sign_in(chat_identity("telegram", "654321"), "bot", "bot")
    _, max_owner, _ = c.sign_in(chat_identity("max", "654321"), "bot", "bot")
    assert telegram_owner.account_id != max_owner.account_id
