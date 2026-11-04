from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from test_bot import stack

from nanomuse_cloud import oauth


@pytest.mark.parametrize("provider", ["google", "yandex", "github"])
async def test_mobile_oauth_returns_a_usable_account_session(monkeypatch, provider):
    monkeypatch.setenv("CLOUD_OAUTH_" + provider.upper() + "_ID", "fixture-id")
    monkeypatch.setenv("CLOUD_OAUTH_" + provider.upper() + "_SECRET", "fixture-secret")
    monkeypatch.setattr(oauth, "fetch_profile", lambda *args: {"email": "signin@example.test"})
    app, _cloud = stack(public_base="https://cloud.test")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://cloud.test") as client:
        start = await client.get("/v1/auth/oauth/" + provider + "/start", headers={"User-Agent": "iPhone Mobile YaBrowser"})
        assert start.status_code == 302
        query = parse_qs(urlsplit(start.headers["location"]).query)
        cookie = start.headers["set-cookie"]
        assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
        callback = await client.get("/v1/auth/oauth/" + provider + "/callback", params={"code": "fixture-code", "state": query["state"][0]})
        assert callback.status_code == 302
        assert callback.headers["cache-control"] == "no-store"
        key = parse_qs(urlsplit(callback.headers["location"]).fragment)["sso"][0]
        me = await client.get("/v1/me", headers={"Authorization": "Bearer " + key})
        assert me.status_code == 200
        assert me.json()["account"]["id"]
        repeated = await client.get("/v1/auth/oauth/" + provider + "/callback", params={"code": "fixture-code", "state": query["state"][0]})
        assert "auth_error=state" in repeated.headers["location"]


async def test_the_provider_list_offers_github_and_keeps_the_widget_out(monkeypatch):
    """GitHub is a redirect provider like Google; Telegram's widget never appears in the
    list of buttons that expect a redirect, and an unknown name is not a provider at all."""
    monkeypatch.setenv("CLOUD_OAUTH_GITHUB_ID", "fixture-id")
    monkeypatch.setenv("CLOUD_OAUTH_GITHUB_SECRET", "fixture-secret")
    monkeypatch.setenv("CLOUD_OAUTH_TELEGRAM_TOKEN", "fixture-token")
    monkeypatch.setenv("CLOUD_OAUTH_TELEGRAM_BOT", "fixture_bot")
    app, _cloud = stack()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://cloud.test") as client:
        listed = (await client.get("/v1/auth/oauth/providers")).json()["providers"]
        assert [p["name"] for p in listed] == ["github"]
        assert listed[0]["kind"] == "redirect"
        assert (await client.get("/v1/auth/oauth/telegram/start")).status_code == 404
        assert (await client.get("/v1/auth/oauth/twitter/start")).status_code == 404


def _telegram_widget(token: str, user_id: str) -> dict[str, str]:
    import hashlib
    import hmac
    import time

    data = {
        "id": user_id,
        "first_name": "Лика",
        "username": "lika",
        "auth_date": str(int(time.time())),
    }
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hashlib.sha256(token.encode()).digest()
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return data


async def test_telegram_widget_signs_in_the_account_the_bot_already_knows(monkeypatch):
    """The widget proves the Telegram identity with the bot token; that identity opens the
    web session, so an account born in a chat needs no password and no second registration.
    A wrong signature is refused, and an identity with no account is refused too — the door
    shows identities, it does not merge accounts by itself."""
    monkeypatch.setenv("CLOUD_OAUTH_TELEGRAM_TOKEN", "fixture-token")
    monkeypatch.setenv("CLOUD_OAUTH_TELEGRAM_BOT", "fixture_bot")
    app, cloud = stack(public_base="https://cloud.test")
    from nanomuse_cloud.service import chat_identity

    cloud.sign_in(chat_identity("telegram", "5211558537"), "Telegram", "test")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://cloud.test") as client:
        good = await client.post("/v1/auth/oauth/telegram", json=_telegram_widget("fixture-token", "5211558537"))
        assert good.status_code == 200, good.text
        assert "set-cookie" in {k.lower() for k in good.headers}
        assert good.headers["cache-control"] == "private, no-store"

        forged = _telegram_widget("fixture-token", "5211558537")
        forged["hash"] = "0" * 64
        bad = await client.post("/v1/auth/oauth/telegram", json=forged)
        assert bad.status_code == 401
        assert bad.json()["error"]["code"] == "invalid_signature"

        unknown = await client.post("/v1/auth/oauth/telegram", json=_telegram_widget("fixture-token", "5211558538"))
        assert unknown.status_code == 409
        assert unknown.json()["error"]["code"] == "telegram_not_linked"
