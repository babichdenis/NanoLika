from urllib.parse import parse_qs, urlparse

import pytest
from test_bot import BOT, CHAT, client_of, sign_in_with_email, stack
from test_link_codes import auth


@pytest.mark.parametrize("channel,host,prefix", [("telegram", "t.me", "TG"), ("max", "max.ru", "MX")])
async def test_start_link_connects_the_same_account_once(channel, host, prefix):
    app, cloud = stack()
    async with client_of(app) as client:
        account = await sign_in_with_email(client, cloud, "qr@example.test")
        key = account["api_key"]
        response = await client.post("/v1/link/start", headers=auth(key), json={"channel": channel})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        data = response.json()
        url = urlparse(data["url"])
        assert url.scheme == "https" and url.netloc == host
        token = parse_qs(url.query)["start"][0].removeprefix("nl_")
        assert token.startswith(prefix) and len(token) == 32
        assert key not in data["url"]
        assert data["expires_in_s"] == 600
        linked = await client.post("/v1/bot/link", headers={"X-Bot-Token": BOT}, json={"channel": channel, "chat_id": CHAT, "code": token})
        assert linked.status_code == 200, linked.text
        rows = (await client.get("/v1/link/channels", headers=auth(key))).json()
        assert rows[0]["channel"] == channel
        repeated = await client.post(
            "/v1/bot/link", headers={"X-Bot-Token": BOT}, json={"channel": channel, "chat_id": "7007007007", "code": token}
        )
        assert repeated.status_code == 400


async def test_start_link_requires_signin_and_rejects_unknown_channels():
    app, cloud = stack()
    async with client_of(app) as client:
        assert (await client.post("/v1/link/start", json={"channel": "max"})).status_code == 401
        account = await sign_in_with_email(client, cloud, "qr-channel@example.test")
        assert (await client.post("/v1/link/start", headers=auth(account["api_key"]), json={"channel": "desktop"})).status_code == 400


async def test_a_new_link_retires_the_previous_qr_and_expired_links_fail(monkeypatch):
    import nanomuse_cloud.db as db_module

    app, cloud = stack()
    async with client_of(app) as client:
        account = await sign_in_with_email(client, cloud, "qr-expiry@example.test")

        async def issue():
            data = (await client.post("/v1/link/start", headers=auth(account["api_key"]), json={"channel": "max"})).json()
            return parse_qs(urlparse(data["url"]).query)["start"][0][3:]

        first, second = await issue(), await issue()
        headers = {"X-Bot-Token": BOT}
        body = {"channel": "max", "chat_id": CHAT, "code": first}
        assert (await client.post("/v1/bot/link", headers=headers, json=body)).status_code == 400
        future = db_module.now() + 601
        monkeypatch.setattr(db_module, "now", lambda: future)
        body["code"] = second
        assert (await client.post("/v1/bot/link", headers=headers, json=body)).status_code == 400
