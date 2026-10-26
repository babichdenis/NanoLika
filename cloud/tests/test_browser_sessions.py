import httpx
from test_bot import sign_in_with_email, stack

from nanomuse_cloud.browser_auth import SESSION_COOKIE

ORIGIN = "https://nanolika.ru"


def client_of(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://cloud.test")


async def test_email_signin_sets_host_only_cookie_and_site_identity_without_browser_storage():
    app, cloud = stack(public_base="https://cloud.test")
    async with client_of(app) as client:
        data = await sign_in_with_email(client, cloud, "browser@example.test")
        assert data["status"] == 200
        cookie = next(c for c in client.cookies.jar if c.name == SESSION_COOKIE)
        assert cookie.secure and not cookie.domain_specified and cookie.path == "/"
        assert cookie.has_nonstandard_attr("HttpOnly")
        response = await client.get("/v1/auth/site-session", headers={"Origin": ORIGIN})
        assert response.json()["state"] == "signed-in"
        assert response.headers["access-control-allow-origin"] == ORIGIN
        assert response.headers["access-control-allow-credentials"] == "true"
        assert "no-store" in response.headers["cache-control"]
        assert data["api_key"] not in response.text
        # This cookie does not authorize other APIs or charges without a Bearer header.
        assert (await client.get("/v1/me")).status_code == 401
        forbidden = await client.get("/v1/auth/site-session", headers={"Origin": "https://foreign.example"})
        assert forbidden.status_code == 403
        assert "access-control-allow-origin" not in forbidden.headers


async def test_site_logout_checks_origin_and_type_then_revokes_the_real_web_key():
    app, cloud = stack(public_base="https://cloud.test")
    async with client_of(app) as client:
        data = await sign_in_with_email(client, cloud, "logout@example.test")
        key = {"Authorization": "Bearer " + data["api_key"]}
        for headers in [{}, {"Origin": "https://foreign.example"}, {"Origin": ORIGIN, "Content-Type": "text/plain"}]:
            response = await client.post("/v1/auth/site-session/logout", headers=headers, content="{}")
            assert response.status_code == 403
            assert (await client.get("/v1/me", headers=key)).status_code == 200
        response = await client.post("/v1/auth/site-session/logout", headers={"Origin": ORIGIN}, json={})
        assert response.json()["state"] == "signed-out"
        assert (await client.get("/v1/me", headers=key)).status_code == 401
        assert SESSION_COOKIE not in client.cookies
        assert (await client.get("/v1/auth/site-session")).json()["state"] == "signed-out"


async def test_legacy_bearer_session_can_establish_cookie_without_a_new_signin():
    app, cloud = stack(public_base="https://cloud.test")
    async with client_of(app) as client:
        data = await sign_in_with_email(client, cloud, "legacy@example.test")
        client.cookies.clear()
        response = await client.post("/v1/auth/browser-session", headers={"Authorization": "Bearer " + data["api_key"]}, json={})
        assert response.status_code == 200
        assert (await client.get("/v1/auth/site-session")).json()["state"] == "signed-in"
        preflight = await client.options(
            "/v1/auth/site-session/logout", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"}
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == ORIGIN


async def test_a_forged_session_cookie_is_cleared_and_never_trusted():
    app, _cloud = stack(public_base="https://cloud.test")
    async with client_of(app) as client:
        client.cookies.set(SESSION_COOKIE, "nm_invalid", domain="cloud.test", path="/")
        response = await client.get("/v1/auth/site-session", headers={"Origin": ORIGIN})
        assert response.json()["state"] == "signed-out"
        assert "Max-Age=0" in response.headers["set-cookie"]


async def test_oauth_cookie_is_available_to_the_website_for_both_providers(monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    from nanomuse_cloud import oauth

    for provider in ("google", "yandex"):
        monkeypatch.setenv("CLOUD_OAUTH_" + provider.upper() + "_ID", "fixture-id")
        monkeypatch.setenv("CLOUD_OAUTH_" + provider.upper() + "_SECRET", "fixture-secret")
        monkeypatch.setattr(oauth, "fetch_profile", lambda *args: {"email": "oauth@example.test"})
        app, _cloud = stack(public_base="https://cloud.test")
        async with client_of(app) as client:
            start = await client.get("/v1/auth/oauth/" + provider + "/start")
            state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
            callback = await client.get("/v1/auth/oauth/" + provider + "/callback", params={"code": "fixture", "state": state})
            assert callback.status_code == 302
            assert SESSION_COOKIE in client.cookies
            status = await client.get("/v1/auth/site-session", headers={"Origin": ORIGIN})
            assert status.json()["state"] == "signed-in"
