"""Website cookies are opaque, finite and subordinate to the original sign-in."""

from __future__ import annotations

from contextlib import asynccontextmanager

from test_bot import sign_in_with_email, stack
from test_browser_sessions import ORIGIN, client_of

from nanomuse_cloud.browser_auth import SESSION_COOKIE
from nanomuse_cloud.browser_session_store import MAX_AGE_S, PREFIX, BrowserSessionStore, browser_store, digest


@asynccontextmanager
async def signed_in():
    app, cloud = stack(public_base="https://cloud.test")
    client = client_of(app)
    async with client:
        data = await sign_in_with_email(client, cloud, "opaque@example.test")
        yield client, cloud, data, client.cookies.get(SESSION_COOKIE)


async def test_cookie_is_hash_only_and_cannot_authorize_bearer_routes():
    async with signed_in() as (client, cloud, data, token):
        assert token.startswith(PREFIX)
        assert token != data["api_key"]
        with cloud.db.tx() as connection:
            row = connection.execute("SELECT * FROM browser_sessions").fetchone()
        assert row["token_hash"] == digest(token)
        assert token not in str(dict(row)) and data["api_key"] not in str(dict(row))
        assert (await client.get("/v1/me", headers={"Authorization": "Bearer " + token})).status_code == 401


async def test_parent_revocation_disables_cookie_and_clears_browser():
    async with signed_in() as (client, cloud, data, token):
        cloud.sign_out(cloud.authenticate(data["api_key"]))
        assert browser_store(cloud).caller(token) is None
        response = await client.get("/v1/auth/site-session")
        assert response.json()["state"] == "signed-out"
        assert SESSION_COOKIE not in client.cookies


async def test_parent_expiry_caps_cookie_and_disabled_account_cannot_use_it():
    async with signed_in() as (client, cloud, data, _):
        caller = cloud.authenticate(data["api_key"])
        store = browser_store(cloud)
        expires = int(store.clock()) + 90
        with cloud.db.tx() as connection:
            connection.execute("UPDATE api_keys SET expires_at=? WHERE key_hash=?", (expires, caller.key_hash))
        token, max_age = store.issue(caller)
        assert 0 < max_age <= 90
        with cloud.db.tx() as connection:
            connection.execute("UPDATE accounts SET disabled=1 WHERE id=?", (caller.account_id,))
        assert store.caller(token) is None


async def test_cookie_expiry_is_enforced_on_server_without_sliding_refresh():
    async with signed_in() as (client, cloud, _, token):
        store = browser_store(cloud)
        response = await client.get("/v1/auth/site-session")
        assert response.json()["state"] == "signed-in"
        assert "set-cookie" not in response.headers
        store.clock = lambda: store.migration_start + MAX_AGE_S + 1
        assert store.caller(token) is None


async def test_parent_expiry_invalidates_cookie_before_cookie_expiration():
    async with signed_in() as (_client, cloud, data, token):
        caller = cloud.authenticate(data["api_key"])
        with cloud.db.tx() as connection:
            connection.execute("UPDATE api_keys SET expires_at=1 WHERE key_hash=?", (caller.key_hash,))
        assert browser_store(cloud).caller(token) is None


async def test_signout_all_preserves_only_current_parents_cookie():
    async with signed_in() as (_client, cloud, data, current_token):
        caller = cloud.authenticate(data["api_key"])
        _, other = cloud._issue_key(caller.account_id, "other browser", "code")
        store = browser_store(cloud)
        other_token, _ = store.issue(other)
        cloud.sign_out_all(caller, keep_current=True)
        assert store.caller(current_token) is not None
        assert store.caller(other_token) is None
        cloud.sign_out_all(caller, keep_current=False)
        assert store.caller(current_token) is None


async def test_delete_account_cascades_opaque_sessions():
    async with signed_in() as (client, cloud, data, _):
        cloud.delete_account(cloud.authenticate(data["api_key"]))
        with cloud.db.tx() as connection:
            assert connection.execute("SELECT COUNT(*) FROM browser_sessions").fetchone()[0] == 0


async def test_legacy_cookie_rotates_once_and_does_not_gain_another_month():
    async with signed_in() as (client, cloud, data, _):
        store = browser_store(cloud)
        caller = cloud.authenticate(data["api_key"])
        with cloud.db.tx() as connection:
            connection.execute("UPDATE api_keys SET created_at=? WHERE key_hash=?", (store.migration_start - 1, caller.key_hash))
        store.clock = lambda: store.legacy_until - 60
        client.cookies.clear()
        client.cookies.set(SESSION_COOKIE, data["api_key"], domain="cloud.test", path="/")
        response = await client.get("/v1/auth/site-session", headers={"Origin": ORIGIN})
        assert response.json()["state"] == "signed-in"
        assert client.cookies.get(SESSION_COOKIE).startswith(PREFIX)
        assert "Max-Age=60" in response.headers["set-cookie"]
        restarted = BrowserSessionStore(cloud)
        assert restarted.migration_start == store.migration_start
        assert restarted.legacy_until == store.legacy_until


async def test_legacy_window_is_finite_but_explicit_bearer_can_establish_cookie():
    async with signed_in() as (client, cloud, data, _):
        store = browser_store(cloud)
        store.clock = lambda: store.legacy_until
        client.cookies.clear()
        client.cookies.set(SESSION_COOKIE, data["api_key"], domain="cloud.test", path="/")
        response = await client.get("/v1/auth/site-session")
        assert response.json()["state"] == "signed-out"
        response = await client.post("/v1/auth/browser-session", headers={"Authorization": "Bearer " + data["api_key"]}, json={})
        assert response.status_code == 200
        assert client.cookies.get(SESSION_COOKIE).startswith(PREFIX)


async def test_new_bearer_is_not_accepted_as_legacy_cookie():
    async with signed_in() as (client, cloud, data, _):
        store = browser_store(cloud)
        caller = cloud.authenticate(data["api_key"])
        with cloud.db.tx() as connection:
            connection.execute("UPDATE api_keys SET created_at=? WHERE key_hash=?", (store.migration_start + 1, caller.key_hash))
        client.cookies.clear()
        client.cookies.set(SESSION_COOKIE, data["api_key"], domain="cloud.test", path="/")
        assert (await client.get("/v1/auth/site-session")).json()["state"] == "signed-out"


async def test_oauth_logout_revokes_cookie_without_revoking_parent_bearer():
    async with signed_in() as (client, cloud, data, token):
        response = await client.get("/v1/auth/logout")
        assert response.status_code == 302
        assert browser_store(cloud).caller(token) is None
        assert cloud.authenticate(data["api_key"])


def test_logout_clears_domain_and_legacy_host_only_presence_markers():
    from starlette.responses import Response

    from nanomuse_cloud.browser_auth import COOKIE, browser_marker
    from nanomuse_cloud.config import Settings

    response = browser_marker(Response(), Settings(public_base="https://cloud.nanolika.ru"), signed_in=False)
    markers = [value for value in response.headers.getlist("set-cookie") if value.startswith(COOKIE + "=")]
    assert len(markers) == 2
    assert all("Max-Age=0" in value for value in markers)
    assert any("Domain=.nanolika.ru" in value for value in markers)
    assert any("Domain=" not in value for value in markers)
