"""Letting a chat go: the way out of a link, from the app and from the chat itself.

`link_codes.py` holds the way in (six characters, one use, the chat becomes the account).
Nothing held the way out, and there was none: `db.remove_alias` existed and no door called
it, so a person could tie a Telegram chat to their account and never untie it. These tests
hold the relay to what "unlink" now means.

What the row is decides everything, so it is worth saying once: the bot finds a chat's
account through `account_aliases`. A row that is gone is a chat nobody owns, and the chat's
next message makes a fresh account of its own, exactly as on the first day. That is the
whole mechanism; there is no second place a link is remembered.

The refusal matters as much as the removal. A chat that *made* an account is not an alias
beside the account, it is the account: unlinking it would empty a person's history and
allowance. So it is refused, with a code the app and the bot can both name, and deleting the
account stays where it belongs, on the app's own door.
"""

from __future__ import annotations

import httpx
from test_bot import BOT, CHAT, client_of, sign_in_with_email, stack

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


async def channels(client: httpx.AsyncClient, key: str) -> list[dict]:
    r = await client.get("/v1/link/channels", headers=auth(key))
    assert r.status_code == 200, r.text
    return r.json()


# -- the app's door: DELETE /v1/link/channels/{channel} -------------------------------------


async def test_the_app_cuts_a_linked_chat_loose():
    app, cloud = stack()
    async with client_of(app) as client:
        account = await sign_in_with_email(client, cloud, "cut@example.com")
        key = account["api_key"]
        code = (await link_code(client, key))["code"]
        assert (await link_chat(client, CHAT, code)).status_code == 200
        assert [row["channel"] for row in await channels(client, key)] == ["telegram"]

        gone = await client.delete("/v1/link/channels/telegram", headers=auth(key))
        assert gone.status_code == 200, gone.text
        body = gone.json()
        assert body["unlinked"] == 1
        assert body["channel"] == "telegram"
        # what was there before, so the app can show what the press did
        assert [row["channel"] for row in body["before"]] == ["telegram"]
        assert body["left"] == []
        assert await channels(client, key) == []


async def test_the_bot_forgets_the_chat_after_the_app_cuts_it():
    """The only proof that matters: the bot's own door no longer finds the account."""
    app, cloud = stack()
    async with client_of(app) as client:
        account = await sign_in_with_email(client, cloud, "forget@example.com")
        key = account["api_key"]
        code = (await link_code(client, key))["code"]
        assert (await link_chat(client, CHAT, code)).status_code == 200

        signed = await client.post("/v1/bot/signin", json={"telegram_id": CHAT}, headers=BOT_HEADERS)
        assert signed.json()["account"]["id"] == account["account"]["id"]

        assert (await client.delete("/v1/link/channels/telegram", headers=auth(key))).status_code == 200

        # the chat is a stranger again: its next message makes an account of its own, a new
        # one, and not the account it used to reach
        after = await client.post("/v1/bot/signin", json={"telegram_id": CHAT}, headers=BOT_HEADERS)
        assert after.status_code == 200, after.text
        assert after.json()["account"]["id"] != account["account"]["id"]
        assert after.json()["created"] is True


async def test_cutting_one_messenger_leaves_the_other():
    app, cloud = stack()
    async with client_of(app) as client:
        account = await sign_in_with_email(client, cloud, "both@example.com")
        key = account["api_key"]
        telegram = (await link_code(client, key))["code"]
        assert (await link_chat(client, CHAT, telegram)).status_code == 200
        maximum = (await link_code(client, key))["code"]
        assert (await link_chat(client, "9009009009", maximum, channel="max")).status_code == 200
        assert {row["channel"] for row in await channels(client, key)} == {"telegram", "max"}

        cut = (await client.delete("/v1/link/channels/telegram", headers=auth(key))).json()
        assert cut["unlinked"] == 1
        assert [row["channel"] for row in cut["left"]] == ["max"]
        # the MAX chat still reaches the account; only the Telegram one was let go
        max_signed = await client.post("/v1/bot/signin", json={"chat_id": "9009009009", "channel": "max"}, headers=BOT_HEADERS)
        assert max_signed.json()["account"]["id"] == account["account"]["id"]


async def test_unlinking_something_that_was_never_linked_is_a_plain_answer():
    """`unlinked: 0` says "nothing was tied"; a person pressing a stale button should not
    read an error."""
    app, cloud = stack()
    async with client_of(app) as client:
        account = await sign_in_with_email(client, cloud, "nothing@example.com")
        gone = await client.delete("/v1/link/channels/telegram", headers=auth(account["api_key"]))
        assert gone.status_code == 200, gone.text
        assert gone.json()["unlinked"] == 0
        assert gone.json()["left"] == []


async def test_a_chat_that_made_the_account_is_refused_not_emptied():
    """The account began in a MAX chat, so that chat is the account. Unlinking it would take
    the history and the allowance with it, and that is a deletion, not a goodbye."""
    app, cloud = stack()
    async with client_of(app) as client:
        signed = await client.post("/v1/bot/signin", json={"chat_id": "5555555555", "channel": "max"}, headers=BOT_HEADERS)
        assert signed.status_code == 200, signed.text
        key = signed.json()["api_key"]

        refused = await client.delete("/v1/link/channels/max", headers=auth(key))
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["code"] == "channel_is_the_account"
        # and nothing was taken: the account is still there, still reachable from the chat
        assert (await client.get("/v1/me", headers=auth(key))).status_code == 200
        again = await client.post("/v1/bot/signin", json={"chat_id": "5555555555", "channel": "max"}, headers=BOT_HEADERS)
        assert again.json()["account"]["id"] == signed.json()["account"]["id"]


async def test_unlinking_needs_the_accounts_own_key():
    app, cloud = stack()
    async with client_of(app) as client:
        nope = await client.delete("/v1/link/channels/telegram")
        assert nope.status_code in (401, 403), nope.text


# -- the bot's door: POST /v1/bot/unlink -----------------------------------------------------


async def test_the_chat_itself_can_let_go():
    app, cloud = stack()
    async with client_of(app) as client:
        account = await sign_in_with_email(client, cloud, "letsgo@example.com")
        key = account["api_key"]
        code = (await link_code(client, key))["code"]
        assert (await link_chat(client, CHAT, code)).status_code == 200

        let_go = await client.post("/v1/bot/unlink", json={"chat_id": CHAT, "channel": "telegram"}, headers=BOT_HEADERS)
        assert let_go.status_code == 200, let_go.text
        assert let_go.json()["unlinked"] is True
        assert await channels(client, key) == []
        # the app's account is intact: the person did not delete anything, one chat left
        assert (await client.get("/v1/me", headers=auth(key))).status_code == 200


async def test_the_bot_door_needs_its_secret():
    app, cloud = stack()
    async with client_of(app) as client:
        nope = await client.post("/v1/bot/unlink", json={"chat_id": CHAT, "channel": "telegram"})
        assert nope.status_code in (401, 403), nope.text


async def test_a_chat_that_is_the_account_hears_why_from_the_bot():
    """No app, no key: the chat asks the bot, and the bot needs a reason it can pass on in
    words rather than a silent failure."""
    app, cloud = stack()
    async with client_of(app) as client:
        signed = await client.post("/v1/bot/signin", json={"chat_id": "7777777777", "channel": "telegram"}, headers=BOT_HEADERS)
        assert signed.status_code == 200
        answer = await client.post("/v1/bot/unlink", json={"chat_id": "7777777777", "channel": "telegram"}, headers=BOT_HEADERS)
        assert answer.status_code == 200, answer.text
        assert answer.json()["unlinked"] is False
        assert answer.json()["reason"] == "channel_is_the_account"


async def test_a_stranger_chat_hears_a_plain_no():
    app, cloud = stack()
    async with client_of(app) as client:
        answer = await client.post("/v1/bot/unlink", json={"chat_id": "1231231234", "channel": "telegram"}, headers=BOT_HEADERS)
        assert answer.status_code == 200, answer.text
        assert answer.json()["unlinked"] is False
        assert answer.json()["reason"] == "not_linked"


# -- the third thing this fixes: a deleted account left its chats tied -----------------------


async def test_deleting_the_account_also_lets_its_chats_go():
    """A deleted account used to leave the alias rows behind, so the bot kept answering a
    person who was gone and a code still named nothing. Deleting takes the rows with it."""
    app, cloud = stack()
    async with client_of(app) as client:
        account = await sign_in_with_email(client, cloud, "delete@example.com")
        key = account["api_key"]
        code = (await link_code(client, key))["code"]
        assert (await link_chat(client, CHAT, code)).status_code == 200

        deleted = await client.post("/v1/auth/delete", headers=auth(key))
        assert deleted.status_code == 204, deleted.text

        # the chat is nobody's: its next message makes a new account instead of finding a
        # ghost
        after = await client.post("/v1/bot/signin", json={"telegram_id": CHAT}, headers=BOT_HEADERS)
        assert after.status_code == 200, after.text
        assert after.json()["created"] is True


async def test_a_second_chat_of_the_same_messenger_can_still_leave():
    """The account began in one Telegram chat; a second Telegram chat was tied by a code.
    `/unlink` in the second one must work: that chat is an ordinary row, and refusing every
    Telegram chat because the account started in Telegram would leave a person stuck.

    This is the shape my first version got wrong: it compared the account's *messenger* to
    the chat's, so both chats of one messenger were refused together.
    """
    app, cloud = stack()
    async with client_of(app) as client:
        # the account begins in this chat, and has no alias row of its own
        first = await client.post(
            "/v1/bot/signin", json={"chat_id": "111000111", "channel": "telegram"}, headers=BOT_HEADERS
        )
        assert first.status_code == 200, first.text
        key = first.json()["api_key"]
        assert first.json()["created"] is True

        # a second Telegram chat joins it by code
        code = (await link_code(client, key))["code"]
        assert (await link_chat(client, "222000222", code)).status_code == 200

        # the chat that made the account cannot leave
        refused = await client.post(
            "/v1/bot/unlink", json={"chat_id": "111000111", "channel": "telegram"}, headers=BOT_HEADERS
        )
        assert refused.status_code == 200, refused.text
        assert refused.json()["reason"] == "channel_is_the_account"

        # the second one can
        let_go = await client.post(
            "/v1/bot/unlink", json={"chat_id": "222000222", "channel": "telegram"}, headers=BOT_HEADERS
        )
        assert let_go.status_code == 200, let_go.text
        assert let_go.json()["unlinked"] is True
        # and the account is untouched
        assert (await client.get("/v1/me", headers=auth(key))).status_code == 200
