"""A chat's account is the messenger and the number, never the number alone.

Telegram and MAX both hand their bot a numeric id, and the two messengers count their users
in separate spaces: the same digits there are two people. So `POST /v1/bot/signin` takes a
`channel` beside the id, and the account it makes is keyed on the pair. A body without one is
a Telegram chat, which is what a bot built before MAX existed sends, and the key it makes is
the very key such a bot made before. What the older rows keep is read as it is: nothing is
rewritten, and a chat linked while every chat was hashed as a Telegram one is still that
account's chat because its row says which messenger the person saw.
"""

from __future__ import annotations

import pytest
from test_bot import BOT, CHAT, client_of, sign_in_with_email, stack

from nanomuse_cloud.identifiers import telegram
from nanomuse_cloud.service import CloudError, chat_identity, chat_surface

BOT_HEADERS = {"X-Bot-Token": BOT}


def auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


async def bot_signin(client, chat: str = CHAT, channel: str | None = None, **extra):
    """A chat's message at the bot's door; no `channel` is the older bot's way."""
    body = {"chat_id": chat, **extra}
    if channel is not None:
        body["channel"] = channel
    return await client.post("/v1/bot/signin", json=body, headers=BOT_HEADERS)


# -- the key itself ------------------------------------------------------------------


def test_the_pair_is_the_key_not_the_number():
    key = b"one-relay-secret"
    both = chat_identity("telegram", CHAT)
    at_max = chat_identity("max", CHAT)
    assert both.value == at_max.value == CHAT
    # the same number, two spaces: an account of one is never the account of the other
    assert both.hash(key) != at_max.hash(key)
    # and the page says which messenger the chat writes from
    assert both.hint == "tg ***5031"
    assert at_max.hint == "max ***5031"


def test_a_chat_without_a_messenger_is_a_telegram_one():
    assert chat_surface("") == "telegram"
    assert chat_surface("  ") == "telegram"
    assert chat_surface("MAX") == "max"
    assert chat_surface("Telegram") == "telegram"


@pytest.mark.parametrize("channel", ["vk", "telegram2", "max2"])
def test_a_channel_is_one_of_the_two_messengers(channel: str):
    with pytest.raises(CloudError) as refused:
        chat_surface(channel)
    assert refused.value.status == 400
    assert refused.value.code == "bad_channel"


# -- the door ------------------------------------------------------------------------


async def test_one_number_on_two_messengers_is_two_accounts():
    app, _cloud = stack()
    async with client_of(app) as client:
        on_telegram = await bot_signin(client, channel="telegram")
        assert on_telegram.status_code == 200, on_telegram.text
        on_max = await bot_signin(client, channel="max")
        assert on_max.status_code == 200, on_max.text
        tg, mx = on_telegram.json(), on_max.json()
        # the same digits, made twice, by two people
        assert tg["created"] is True and mx["created"] is True
        assert tg["account"]["id"] != mx["account"]["id"]
        assert tg["account"]["channel"] == "telegram"
        assert mx["account"]["channel"] == "max"
        assert tg["account"]["hint"] == "tg ***5031"
        assert mx["account"]["hint"] == "max ***5031"
        # two allowances and two keys: neither person spends the other's
        assert tg["tokens"]["granted"] == mx["tokens"]["granted"] == 1000
        assert tg["api_key"] != mx["api_key"]


async def test_a_second_message_finds_the_same_account_on_each_messenger():
    app, _cloud = stack()
    async with client_of(app) as client:
        first_tg = (await bot_signin(client, channel="telegram")).json()
        first_mx = (await bot_signin(client, channel="max")).json()
        again_tg = (await bot_signin(client, channel="telegram")).json()
        again_mx = (await bot_signin(client, channel="max")).json()
        assert again_tg["created"] is False and again_mx["created"] is False
        assert again_tg["account"]["id"] == first_tg["account"]["id"]
        assert again_mx["account"]["id"] == first_mx["account"]["id"]
        # and the two are still two
        assert again_tg["account"]["id"] != again_mx["account"]["id"]
        # no second bonus either way
        assert again_tg["tokens"]["granted"] == again_mx["tokens"]["granted"] == 1000


async def test_a_body_without_a_channel_is_the_chat_the_older_bot_made():
    app, _cloud = stack()
    async with client_of(app) as client:
        # a bot built before MAX existed sends the id alone, and calls it telegram_id
        older = await client.post("/v1/bot/signin", json={"telegram_id": CHAT}, headers=BOT_HEADERS)
        assert older.status_code == 200, older.text
        assert older.json()["created"] is True
        named = (await bot_signin(client, channel="telegram")).json()
        # the same account: the old path is the Telegram space, not a third one
        assert named["created"] is False
        assert named["account"]["id"] == older.json()["account"]["id"]
        # and it is not the MAX chat of the same number
        on_max = (await bot_signin(client, channel="max")).json()
        assert on_max["account"]["id"] != older.json()["account"]["id"]


async def test_a_max_chat_never_inherits_the_telegram_account_of_its_number():
    app, _cloud = stack()
    async with client_of(app) as client:
        tg = (await bot_signin(client, channel="telegram")).json()
        # the MAX chat of the same number hears nothing about that account
        status = await client.get("/v1/bot/link", params={"channel": "max", "chat_id": CHAT}, headers=BOT_HEADERS)
        assert status.status_code == 200, status.text
        assert status.json() == {"linked": False, "channel": "max"}
        mx = (await bot_signin(client, channel="max")).json()
        assert mx["created"] is True
        assert mx["account"]["id"] != tg["account"]["id"]


async def test_a_channel_that_is_not_a_messenger_makes_no_account():
    app, _cloud = stack()
    async with client_of(app) as client:
        refused = await bot_signin(client, channel="vk")
        assert refused.status_code == 400
        assert refused.json()["error"]["code"] == "bad_channel"
        # nothing was made by the refusal
        assert (await bot_signin(client, channel="telegram")).json()["created"] is True


# -- the code a chat hands the app ---------------------------------------------------


async def test_the_code_a_chat_hands_out_is_made_for_its_own_messenger():
    app, cloud = stack()
    async with client_of(app) as client:
        on_telegram = (await bot_signin(client, channel="telegram")).json()
        on_max = (await bot_signin(client, channel="max")).json()
        r = await client.post(
            "/v1/bot/link-code",
            json={"chat_id": CHAT, "channel": "max"},
            headers=BOT_HEADERS,
        )
        assert r.status_code == 200, r.text
        code = r.json()["code"]
        # the app types the code with its own sign-in and lands in the MAX account
        app_side = await sign_in_with_email(client, cloud, "max-owner@example.com", link=code)
        assert app_side["status"] == 200, app_side
        assert app_side["created"] is False
        assert app_side["account"]["id"] == on_max["account"]["id"]
        assert app_side["account"]["id"] != on_telegram["account"]["id"]
        # and the Telegram chat of the same number is still its own account
        again_tg = (await bot_signin(client, channel="telegram")).json()
        assert again_tg["account"]["id"] == on_telegram["account"]["id"]


async def test_a_code_typed_by_a_max_chat_joins_the_max_chat_not_the_telegram_one():
    app, cloud = stack()
    async with client_of(app) as client:
        on_telegram = (await bot_signin(client, channel="telegram")).json()
        # somebody signed in to the app and read six characters off its screen
        account = await sign_in_with_email(client, cloud, "joined@example.com")
        assert account["created"] is True
        code = (await client.post("/v1/link/code", headers=auth(account["api_key"]))).json()["code"]
        linked = await client.post(
            "/v1/bot/link",
            json={"channel": "max", "chat_id": CHAT, "code": code, "identifier": ""},
            headers=BOT_HEADERS,
        )
        assert linked.status_code == 200, linked.text
        assert linked.json()["channel"] == "max"
        assert linked.json()["label"] == "max ***5031"
        # the MAX chat now spends that account's allowance
        on_max = (await bot_signin(client, channel="max")).json()
        assert on_max["created"] is False
        assert on_max["account"]["id"] == account["account"]["id"]
        assert [row["channel"] for row in on_max["account"]["linked"]] == ["max"]
        # the Telegram chat of the same number was not touched by it
        again_tg = (await bot_signin(client, channel="telegram")).json()
        assert again_tg["account"]["id"] == on_telegram["account"]["id"]
        assert again_tg["account"]["linked"] == []


# -- the rows written before the messenger was part of the key -----------------------


async def test_a_chat_linked_before_the_messenger_was_part_of_the_key_is_still_found():
    """0.30 wrote such a row in the Telegram space and recorded the messenger it saw. It is
    read as the MAX chat it was, and its account is reached instead of a second one being
    made by the first message after the change."""
    app, cloud = stack()
    async with client_of(app) as client:
        account = await sign_in_with_email(client, cloud, "early@example.com")
        assert account["created"] is True
        legacy_hash = telegram(CHAT).hash(cloud.s.hmac_key)
        assert cloud.db.add_alias(
            legacy_hash,
            account["account"]["id"],
            "telegram",
            "tg ***5031",
            surface="max",
            identifier="@denis",
        )
        status = await client.get("/v1/bot/link", params={"channel": "max", "chat_id": CHAT}, headers=BOT_HEADERS)
        assert status.status_code == 200, status.text
        assert status.json()["linked"] is True
        assert status.json()["channel"] == "max"
        # the door opens the account that row names, and makes no second one
        on_max = (await bot_signin(client, channel="max")).json()
        assert on_max["created"] is False
        assert on_max["account"]["id"] == account["account"]["id"]
        # That row is filed in the Telegram space, so the Telegram chat of the same number is
        # the same key and lands in the same account: an old row cannot be split into two
        # without rewriting it, and rewriting it is not what this change does. What is new is
        # that no MAX chat is filed there any more, so this can only be history, never a new
        # merge.
        on_telegram = (await bot_signin(client, channel="telegram")).json()
        assert on_telegram["account"]["id"] == account["account"]["id"]
        assert on_telegram["created"] is False
