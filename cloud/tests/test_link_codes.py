"""The short code that joins a chat to the account its person already has (relay 0.30).

The app asks with its own key (`POST /v1/link/code`), the person sends six characters to the
bot, the bot hands them back with its shared secret (`POST /v1/bot/link`). What these tests
hold the relay to: the code's shape and lifetime, one use, a fresh code retiring the one
before it, the chat that becomes the account (so the bot's own door finds it afterwards),
and the two doors that need their own proof.
"""

from __future__ import annotations

import time

import httpx
import pytest
from test_bot import BOT, CHAT, client_of, sign_in_with_email, stack

from nanomuse_cloud.service import CHANNEL_CODE_ALPHABET, CHANNEL_CODE_LENGTH, CHANNEL_CODE_TTL_S

BOT_HEADERS = {"X-Bot-Token": BOT}


def auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


async def link_code(client: httpx.AsyncClient, key: str) -> dict:
    r = await client.post("/v1/link/code", headers=auth(key))
    assert r.status_code == 200, r.text
    return r.json()


async def link_chat(client: httpx.AsyncClient, chat: str, code: str, **extra) -> httpx.Response:
    body = {"chat_id": chat, "code": code, **extra}
    return await client.post("/v1/bot/link", json=body, headers=BOT_HEADERS)


async def test_the_code_is_six_characters_without_lookalikes():
    app, cloud = stack()
    async with client_of(app) as client:
        account = await sign_in_with_email(client, cloud, "shape@example.com")
        data = await link_code(client, account["api_key"])
        code = data["code"]
        assert len(code) == CHANNEL_CODE_LENGTH == 6
        assert set(code) <= set(CHANNEL_CODE_ALPHABET)
        # the alphabet a person reads out loud or copies off a screen: nothing that looks
        # like another character, in either case
        assert not set("O0I1") & set(code)
        assert data["expires_in_s"] == CHANNEL_CODE_TTL_S == 600
        assert 590 <= data["expires_at"] - int(time.time()) <= 610


async def test_the_chat_types_the_code_and_becomes_that_account():
    app, cloud = stack()
    async with client_of(app) as client:
        account = await sign_in_with_email(client, cloud, "joins@example.com")
        code = (await link_code(client, account["api_key"]))["code"]

        # the chat is nobody's yet
        before = await client.get("/v1/bot/link", params={"chat_id": CHAT}, headers=BOT_HEADERS)
        assert before.status_code == 200
        assert before.json()["linked"] is False

        linked = await link_chat(client, CHAT, code, identifier="@denis")
        assert linked.status_code == 200, linked.text
        assert linked.json()["linked"] is True
        assert linked.json()["channel"] == "telegram"
        assert linked.json()["identifier"] == "@denis"

        # the bot's own door now finds that account for the chat, which is the whole point:
        # what is said in the chat costs that account's allowance and not a second one's
        signed = await client.post("/v1/bot/signin", json={"telegram_id": CHAT}, headers=BOT_HEADERS)
        assert signed.status_code == 200, signed.text
        assert signed.json()["account"]["id"] == account["account"]["id"]
        assert signed.json()["created"] is False
        # and the account page shows both ways in: the address and the chat
        me = (await client.get("/v1/me", headers=auth(account["api_key"]))).json()
        assert [row["channel"] for row in me["account"]["linked"]] == ["telegram"]


async def test_a_code_lives_ten_minutes(monkeypatch):
    app, cloud = stack()
    async with client_of(app) as client:
        account = await sign_in_with_email(client, cloud, "ten@example.com")
        code = (await link_code(client, account["api_key"]))["code"]

        # ten minutes and a second later the same six characters are nobody's
        from nanomuse_cloud import db as db_module

        ahead = db_module.now() + CHANNEL_CODE_TTL_S + 1
        monkeypatch.setattr(db_module, "now", lambda: ahead)
        late = await link_chat(client, CHAT, code)
        assert late.status_code == 400
        assert late.json()["error"]["code"] == "link_wrong"

        # a new one, asked for now, still works
        fresh = (await link_code(client, account["api_key"]))["code"]
        assert (await link_chat(client, CHAT, fresh)).status_code == 200


async def test_a_code_is_spent_once():
    app, cloud = stack()
    async with client_of(app) as client:
        account = await sign_in_with_email(client, cloud, "once@example.com")
        code = (await link_code(client, account["api_key"]))["code"]
        assert (await link_chat(client, CHAT, code)).status_code == 200
        second = await link_chat(client, "7007007007", code)
        assert second.status_code == 400
        assert second.json()["error"]["code"] == "link_wrong"


async def test_asking_again_retires_the_earlier_code():
    app, cloud = stack()
    async with client_of(app) as client:
        account = await sign_in_with_email(client, cloud, "again@example.com")
        first = (await link_code(client, account["api_key"]))["code"]
        second = (await link_code(client, account["api_key"]))["code"]
        retired = await link_chat(client, CHAT, first)
        assert retired.status_code == 400
        assert retired.json()["error"]["code"] == "link_wrong"
        assert (await link_chat(client, CHAT, second)).status_code == 200


async def test_the_account_lists_the_chats_it_has():
    app, cloud = stack()
    async with client_of(app) as client:
        account = await sign_in_with_email(client, cloud, "chats@example.com")
        key = account["api_key"]
        assert (await client.get("/v1/link/channels", headers=auth(key))).json() == []

        code = (await link_code(client, key))["code"]
        assert (await link_chat(client, CHAT, code, identifier="@denis")).status_code == 200
        rows = (await client.get("/v1/link/channels", headers=auth(key))).json()
        assert len(rows) == 1
        assert rows[0]["channel"] == "telegram"
        assert rows[0]["identifier"] == "@denis"
        assert rows[0]["label"] == "tg ***5031"
        assert rows[0]["linked_at"] > 0

        # a second chat, another messenger, on the same account
        second = (await link_code(client, key))["code"]
        assert (await link_chat(client, "7007007007", second, channel="max")).status_code == 200
        rows = (await client.get("/v1/link/channels", headers=auth(key))).json()
        assert [row["channel"] for row in rows] == ["telegram", "max"]

    # an account whose chat came first lists that chat, with no address beside it
    other, other_cloud = stack()
    async with client_of(other) as client:
        chat = (await client.post("/v1/bot/signin", json={"telegram_id": CHAT}, headers=BOT_HEADERS)).json()
        rows = (await client.get("/v1/link/channels", headers=auth(chat["api_key"]))).json()
        assert rows == [
            {
                "channel": "telegram",
                "identifier": "",
                "label": "tg ***5031",
                "linked_at": rows[0]["linked_at"],
            }
        ]


async def test_a_chat_with_an_account_of_its_own_is_not_taken_over():
    app, cloud = stack()
    async with client_of(app) as client:
        account = await sign_in_with_email(client, cloud, "busy@example.com")
        code = (await link_code(client, account["api_key"]))["code"]
        # the chat talked to the bot before: it is an account of its own already
        await client.post("/v1/bot/signin", json={"telegram_id": CHAT}, headers=BOT_HEADERS)
        taken = await link_chat(client, CHAT, code)
        assert taken.status_code == 409
        assert taken.json()["error"]["code"] == "link_taken"
        # the refusal did not spend the code: the chat it was meant for still gets it
        assert (await link_chat(client, "7007007007", code)).status_code == 200


async def test_a_wrong_code_leaves_the_chat_alone():
    app, cloud = stack()
    async with client_of(app) as client:
        account = await sign_in_with_email(client, cloud, "wrong@example.com")
        wrong = await link_chat(client, CHAT, "ZZZZZZ")
        assert wrong.status_code == 400
        assert wrong.json()["error"]["code"] == "link_wrong"
        known = await client.get("/v1/bot/link", params={"chat_id": CHAT}, headers=BOT_HEADERS)
        assert known.json()["linked"] is False
        # the account is untouched: a real code still joins
        code = (await link_code(client, account["api_key"]))["code"]
        assert (await link_chat(client, CHAT, code)).status_code == 200


async def test_the_account_routes_need_a_key():
    app, cloud = stack()
    async with client_of(app) as client:
        r = await client.post("/v1/link/code")
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "bad_key"
        r = await client.get("/v1/link/channels")
        assert r.status_code == 401


async def test_the_bot_routes_need_the_bots_token():
    app, cloud = stack()
    async with client_of(app) as client:
        account = await sign_in_with_email(client, cloud, "token@example.com")
        code = (await link_code(client, account["api_key"]))["code"]
        r = await client.post("/v1/bot/link", json={"chat_id": CHAT, "code": code})
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "bad_bot_token"
        r = await client.get("/v1/bot/link", params={"chat_id": CHAT})
        assert r.status_code == 401
        # nothing was linked behind the refusal
        assert (await client.get("/v1/link/channels", headers=auth(account["api_key"]))).json() == []


async def test_linking_a_chat_can_be_switched_off():
    app, cloud = stack(link_code_ttl_s=0)
    async with client_of(app) as client:
        account = await sign_in_with_email(client, cloud, "off@example.com")
        code = (await link_code(client, account["api_key"]))["code"]
        r = await link_chat(client, CHAT, code)
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "link_off"


@pytest.mark.parametrize("channel", ["vk", "telegram2", "max2"])
async def test_a_channel_is_one_of_the_two_messengers(channel: str):
    app, cloud = stack()
    async with client_of(app) as client:
        account = await sign_in_with_email(client, cloud, "messenger@example.com")
        code = (await link_code(client, account["api_key"]))["code"]
        r = await link_chat(client, CHAT, code, channel=channel)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "bad_channel"


async def test_a_link_without_a_channel_names_telegram():
    """The bot's older half of the door says nothing about a messenger: a chat is a chat."""
    app, cloud = stack()
    async with client_of(app) as client:
        account = await sign_in_with_email(client, cloud, "plain@example.com")
        code = (await link_code(client, account["api_key"]))["code"]
        r = await client.post("/v1/bot/link", json={"chat_id": CHAT, "code": code}, headers=BOT_HEADERS)
        assert r.status_code == 200, r.text
        assert r.json()["channel"] == "telegram"


async def test_the_six_characters_are_read_in_any_case_and_only_once():
    """One table, one meaning: a code adds a way into the account that made it, whichever
    end of the conversation typed it. A phone's keyboard offers lower case, so both cases
    are the same code, and the second use of it finds nothing."""
    app, cloud = stack()
    async with client_of(app) as client:
        account = await sign_in_with_email(client, cloud, "case@example.com")
        code = (await link_code(client, account["api_key"]))["code"]
        typed = await link_chat(client, CHAT, code.lower(), identifier="denis")
        assert typed.status_code == 200, typed.text
        # a handle is written the way the messenger writes it, whatever the bot sent
        assert typed.json()["identifier"] == "@denis"
        again = await link_chat(client, "7007007007", code)
        assert again.status_code == 400
        assert again.json()["error"]["code"] == "link_wrong"
