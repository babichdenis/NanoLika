"""The Telegram door: a chat becomes an account of its own, with the allowance, once."""

from __future__ import annotations

import httpx
import pytest

from nanomuse_cloud.api import create_app
from nanomuse_cloud.config import Settings
from nanomuse_cloud.db import Database
from nanomuse_cloud.identifiers import BadIdentifier, parse, telegram
from nanomuse_cloud.senders import LogSender
from nanomuse_cloud.service import Cloud

BOT = "bot-secret-for-tests"
CHAT = "5188775031"


def stack(**overrides) -> tuple[object, Cloud]:
    kw = dict(
        database=":memory:",
        secret="test-secret",
        admin_token="admin",
        signup_tokens=1000,
        daily_cap_tokens=100_000,
        per_minute_requests=100,
        public_base="http://cloud.test",
        bot_signin_token=BOT,
    )
    kw.update(overrides)
    settings = Settings(**kw)
    cloud = Cloud(settings, Database(":memory:"), LogSender())
    return create_app(settings, cloud), cloud


def client_of(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cloud.test")


def test_a_telegram_chat_is_an_identifier_of_its_own():
    ident = telegram(CHAT)
    assert ident.channel == "telegram"
    assert ident.value == CHAT
    assert ident.hint == "tg ***5031"
    # a chat is not an address: the same digits as a number are a different account
    assert ident.hash(b"k") != parse("+" + CHAT).hash(b"k")
    for bad in ("", "abc", "12", "1" * 21, "-5188775031"):
        with pytest.raises(BadIdentifier):
            telegram(bad)


async def test_the_bot_makes_an_account_for_a_chat():
    app, cloud = stack()
    async with client_of(app) as client:
        r = await client.post("/v1/bot/signin", json={"telegram_id": CHAT}, headers={"X-Bot-Token": BOT})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["created"] is True
        assert data["api_key"].startswith("nm_")
        assert data["account"]["hint"] == "tg ***5031"
        assert data["tokens"]["granted"] == 1000
        # the key is an ordinary key: /v1/me knows the account it made
        me = await client.get("/v1/me", headers={"Authorization": f"Bearer {data['api_key']}"})
        assert me.status_code == 200, me.text
        assert me.json()["account"]["id"] == data["account"]["id"]


async def test_a_second_message_finds_the_same_account_and_no_second_bonus():
    app, _cloud = stack()
    async with client_of(app) as client:
        first = (await client.post("/v1/bot/signin", json={"telegram_id": CHAT}, headers={"X-Bot-Token": BOT})).json()
        again = await client.post("/v1/bot/signin", json={"telegram_id": CHAT}, headers={"X-Bot-Token": BOT})
        assert again.status_code == 200, again.text
        second = again.json()
        assert second["created"] is False
        assert second["account"]["id"] == first["account"]["id"]
        assert second["tokens"]["granted"] == 1000
        assert second["api_key"] != first["api_key"]  # a key per chat, both the same account


async def test_the_door_needs_the_bots_token():
    app, _cloud = stack()
    async with client_of(app) as client:
        assert (await client.post("/v1/bot/signin", json={"telegram_id": CHAT})).status_code == 401
        wrong = await client.post("/v1/bot/signin", json={"telegram_id": CHAT}, headers={"X-Bot-Token": "nope"})
        assert wrong.status_code == 401
        assert wrong.json()["error"]["code"] == "bad_bot_token"
        bad_id = await client.post("/v1/bot/signin", json={"telegram_id": "abc"}, headers={"X-Bot-Token": BOT})
        assert bad_id.status_code == 400
        assert bad_id.json()["error"]["code"] == "bad_identifier"


async def test_a_relay_without_the_secret_keeps_the_door_shut():
    app, _cloud = stack(bot_signin_token="")
    async with client_of(app) as client:
        r = await client.post("/v1/bot/signin", json={"telegram_id": CHAT}, headers={"X-Bot-Token": ""})
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "bot_off"


async def test_closing_the_bot_to_newcomers_keeps_the_ones_that_exist():
    app, _cloud = stack()
    async with client_of(app) as client:
        first = (await client.post("/v1/bot/signin", json={"telegram_id": CHAT}, headers={"X-Bot-Token": BOT})).json()
        old = (await client.post("/v1/bot/signin", json={"telegram_id": "7007007007"}, headers={"X-Bot-Token": BOT})).json()
        assert old["created"] is True
    # the switch turned off on the same database
    app2, _cloud2 = stack(bot_signup_open=False)
    async with client_of(app2) as client:
        newcomer = await client.post("/v1/bot/signin", json={"telegram_id": "8008008008"}, headers={"X-Bot-Token": BOT})
        assert newcomer.status_code == 403
        assert newcomer.json()["error"]["code"] == "signup_closed"
    assert first["created"] is True


async def test_the_bot_door_opens_a_relay_that_is_private_for_addresses():
    # SIGNUP_OPEN=0 with an empty members list: no address would be let in, a chat still is
    app, _cloud = stack(signup_open=False)
    async with client_of(app) as client:
        r = await client.post("/v1/bot/signin", json={"telegram_id": CHAT}, headers={"X-Bot-Token": BOT})
        assert r.status_code == 200, r.text
        assert r.json()["created"] is True


def codes_of(cloud: Cloud) -> list[tuple[object, str]]:
    return list(cloud.sender.sent)  # type: ignore[attr-defined]


async def sign_in_with_email(client, cloud, address: str, link: str = "") -> dict:
    """A sign-in the way the app does it: a code to the mailbox, then verify, optionally
    with the chat's link code beside it."""
    r = await client.post("/v1/auth/code", json={"identifier": address})
    assert r.status_code == 204, r.text
    _ident, code = codes_of(cloud)[-1]
    body = {"identifier": address, "code": code, "device": "desktop"}
    if link:
        body["link"] = link
    r = await client.post("/v1/auth/verify", json=body)
    return {"status": r.status_code, **r.json()}


async def test_the_chat_hands_the_app_a_code_and_the_two_become_one_account():
    app, cloud = stack()
    async with client_of(app) as client:
        chat = (await client.post("/v1/bot/signin", json={"telegram_id": CHAT}, headers={"X-Bot-Token": BOT})).json()
        r = await client.post("/v1/bot/link-code", json={"telegram_id": CHAT}, headers={"X-Bot-Token": BOT})
        assert r.status_code == 200, r.text
        code = r.json()["code"]
        assert len(code) == 8 and r.json()["expires_in_s"] == 900

        app_side = await sign_in_with_email(client, cloud, "denis@example.com", link=code)
        assert app_side["status"] == 200, app_side
        assert app_side["created"] is False
        assert app_side["account"]["id"] == chat["account"]["id"]
        # one allowance, not two: the account kept the figure it was made with
        assert app_side["tokens"]["granted"] == chat["tokens"]["granted"] == 1000
        # the account page can say both ways in rather than showing two accounts
        me = await client.get("/v1/me", headers={"Authorization": f"Bearer {app_side['api_key']}"})
        linked = [row["channel"] for row in me.json()["account"]["linked"]]
        assert linked == ["email"]
        # and the chat still finds that same account afterwards
        again = (await client.post("/v1/bot/signin", json={"telegram_id": CHAT}, headers={"X-Bot-Token": BOT})).json()
        assert again["account"]["id"] == chat["account"]["id"]
        assert again["created"] is False


async def test_a_link_code_is_spent_once():
    app, cloud = stack()
    async with client_of(app) as client:
        await client.post("/v1/bot/signin", json={"telegram_id": CHAT}, headers={"X-Bot-Token": BOT})
        code = (await client.post("/v1/bot/link-code", json={"telegram_id": CHAT}, headers={"X-Bot-Token": BOT})).json()["code"]
        first = await sign_in_with_email(client, cloud, "one@example.com", link=code)
        assert first["status"] == 200
        second = await sign_in_with_email(client, cloud, "two@example.com", link=code)
        assert second["status"] == 400
        assert second["error"]["code"] == "link_wrong"


async def test_a_wrong_link_code_leaves_the_address_to_its_own_sign_in():
    app, cloud = stack()
    async with client_of(app) as client:
        refused = await sign_in_with_email(client, cloud, "solo@example.com", link="AAAAAAAA")
        assert refused["status"] == 400
        assert refused["error"]["code"] == "link_wrong"
        # the same address signs in plainly afterwards: the refusal cost it nothing
        plain = await sign_in_with_email(client, cloud, "solo@example.com")
        assert plain["status"] == 200
        assert plain["created"] is True


async def test_an_address_with_an_account_of_its_own_is_not_taken_over():
    app, cloud = stack()
    async with client_of(app) as client:
        own = await sign_in_with_email(client, cloud, "busy@example.com")
        assert own["created"] is True
        await client.post("/v1/bot/signin", json={"telegram_id": CHAT}, headers={"X-Bot-Token": BOT})
        code = (await client.post("/v1/bot/link-code", json={"telegram_id": CHAT}, headers={"X-Bot-Token": BOT})).json()["code"]
        taken = await sign_in_with_email(client, cloud, "busy@example.com", link=code)
        assert taken["status"] == 409
        assert taken["error"]["code"] == "link_taken"


async def test_linking_can_be_switched_off():
    app, cloud = stack(link_code_ttl_s=0)
    async with client_of(app) as client:
        await client.post("/v1/bot/signin", json={"telegram_id": CHAT}, headers={"X-Bot-Token": BOT})
        r = await client.post("/v1/auth/code", json={"identifier": "off@example.com"})
        assert r.status_code == 204
        _ident, code = codes_of(cloud)[-1]
        r = await client.post(
            "/v1/auth/verify",
            json={"identifier": "off@example.com", "code": code, "device": "desktop", "link": "ABCDEFGH"},
        )
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "link_off"


async def test_the_link_code_endpoint_needs_the_bots_token():
    app, _cloud = stack()
    async with client_of(app) as client:
        r = await client.post("/v1/bot/link-code", json={"telegram_id": CHAT})
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "bad_bot_token"
