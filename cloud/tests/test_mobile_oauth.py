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


async def test_unsupported_telegram_login_returns_a_response_not_a_type_error():
    app, _cloud = stack()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://cloud.test") as client:
        response = await client.post("/v1/auth/oauth/telegram")
        assert response.status_code == 410
        assert response.json()["error"]["code"] == "email_required"
