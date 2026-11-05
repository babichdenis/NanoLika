"""The native handoff never sends an account key through a browser redirect."""

import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from test_bot import stack

from nanomuse_cloud.desktop_oauth import DesktopCodes, desktop_request
from nanomuse_cloud.service import CloudError

VERIFIER = "v" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
CALLBACK = "http://127.0.0.1:49152/nanolika-signin"
STATE = "s" * 43


@pytest.mark.parametrize("provider", ["google", "yandex"])
async def test_browser_handoff_exchange_is_pkce_bound_and_single_use(monkeypatch, provider):
    monkeypatch.setenv(f"CLOUD_OAUTH_{provider.upper()}_ID", "test-client")
    monkeypatch.setenv(f"CLOUD_OAUTH_{provider.upper()}_SECRET", "test-secret")
    monkeypatch.setattr("nanomuse_cloud.oauth.fetch_profile", lambda *args: {"email": "native@example.test"})
    app, cloud = stack(public_base="https://cloud.test")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://cloud.test") as client:
        start = await client.get(
            f"/v1/auth/oauth/{provider}/start",
            params={
                "desktop_callback": CALLBACK,
                "desktop_challenge": CHALLENGE,
                "desktop_state": STATE,
            },
        )
        nonce = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        callback = await client.get(f"/v1/auth/oauth/{provider}/callback", params={"code": "provider-code", "state": nonce})
        target = urlsplit(callback.headers["location"])
        assert target.scheme == "http" and target.netloc == "127.0.0.1:49152"
        answer = parse_qs(target.query)
        assert answer["state"] == [STATE] and set(answer) == {"code", "state"}
        assert "no-store" in callback.headers["cache-control"]
        code = answer["code"][0]
        wrong = await client.post("/v1/auth/desktop/exchange", json={"code": code, "verifier": "x" * 64})
        assert wrong.status_code == 400
        good = await client.post("/v1/auth/desktop/exchange", json={"code": code, "verifier": VERIFIER, "device": "Desktop test"})
        assert good.status_code == 200, good.text
        assert good.json()["api_key"] and good.json()["created"]
        me = await client.get("/v1/me", headers={"Authorization": "Bearer " + good.json()["api_key"]})
        assert me.status_code == 200
        replay = await client.post("/v1/auth/desktop/exchange", json={"code": code, "verifier": VERIFIER})
        assert replay.status_code == 400


@pytest.mark.parametrize(
    "callback",
    [
        "https://evil.example/nanolika-signin",
        "http://localhost:49152/nanolika-signin",
        "http://127.0.0.1:49152/other",
        CALLBACK + "?next=x",
        "http://user@127.0.0.1:49152/nanolika-signin",
    ],
)
def test_callback_rejects_anything_except_native_loopback(callback):
    with pytest.raises(CloudError):
        desktop_request(callback, CHALLENGE, STATE)


def test_expired_code_and_cross_instance_exchange():
    _, cloud = stack()
    now = [1000]
    first = DesktopCodes(cloud.db, clock=lambda: now[0])
    second = DesktopCodes(cloud.db, clock=lambda: now[0])
    code = first.issue("native@example.test", "google", CHALLENGE)
    assert second.consume(code, VERIFIER) == ("native@example.test", "google")
    expired = first.issue("native@example.test", "yandex", CHALLENGE)
    now[0] += 121
    with pytest.raises(CloudError):
        second.consume(expired, VERIFIER)


@pytest.mark.parametrize("provider", ["google", "yandex"])
async def test_provider_denial_returns_to_app_without_account(monkeypatch, provider):
    monkeypatch.setenv(f"CLOUD_OAUTH_{provider.upper()}_ID", "test-client")
    monkeypatch.setenv(f"CLOUD_OAUTH_{provider.upper()}_SECRET", "test-secret")
    app, _ = stack(public_base="https://cloud.test")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://cloud.test") as client:
        start = await client.get(
            f"/v1/auth/oauth/{provider}/start",
            params={
                "desktop_callback": CALLBACK,
                "desktop_challenge": CHALLENGE,
                "desktop_state": STATE,
            },
        )
        nonce = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        response = await client.get(f"/v1/auth/oauth/{provider}/callback", params={"error": "access_denied", "state": nonce})
        assert parse_qs(urlsplit(response.headers["location"]).query) == {"state": [STATE], "error": ["denied"]}


@pytest.mark.parametrize("provider", ["google", "yandex"])
async def test_existing_browser_login_keeps_its_session_flow(monkeypatch, provider):
    monkeypatch.setenv(f"CLOUD_OAUTH_{provider.upper()}_ID", "test-client")
    monkeypatch.setenv(f"CLOUD_OAUTH_{provider.upper()}_SECRET", "test-secret")
    monkeypatch.setattr("nanomuse_cloud.oauth.fetch_profile", lambda *args: {"email": "web@example.test"})
    app, _ = stack(public_base="https://cloud.test")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://cloud.test") as client:
        start = await client.get(f"/v1/auth/oauth/{provider}/start", params={"next": "https://nanolika.ru/"})
        nonce = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        response = await client.get(f"/v1/auth/oauth/{provider}/callback", params={"code": "provider-code", "state": nonce})
        assert response.status_code == 302
        target = urlsplit(response.headers["location"])
        assert target.scheme == "https" and target.netloc == "cloud.test" and target.path == "/app/"
        assert target.fragment.startswith("sso=")
        assert (await client.get("/v1/auth/site-session")).json()["state"] == "signed-in"
