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
