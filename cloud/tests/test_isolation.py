"""P0-8: what one account may spend, what the server checks on its own, and the wall

Four rules, each one a test that fails the moment the rule is broken:

* the money an account has is what there is — the welcome pool is given once, the invite
  bonus is paid once per friend, and neither opens a second lane past the ceilings;
* a spent pool, a spent token ceiling and a spent daily share are refusals, not free answers
  and not a ledger that goes quietly into the red;
* a lapsed tariff or a lapsed connection is noticed by the relay itself, from its own clock;
* one account's key sees nothing of another's chats, devices, balance or devices to revoke,
  and the operator's routes answer only to the operator's token.
"""

from __future__ import annotations

import json
import math

import pytest
from starlette.websockets import WebSocketDisconnect
from test_accounts import auth, make
from test_cloud import sign_up

from nanomuse_cloud.db import now as db_now
from nanomuse_cloud.identifiers import telegram
from nanomuse_cloud.service import Cloud

BOT = "bot-secret-for-tests"
BOT_HEADERS = {"X-Bot-Token": BOT}
ADMIN_HEADERS = {"X-Admin-Token": "admin"}
HELLO = {"type": "hello", "device": {"id": "pc-1", "name": "desk", "kind": "computer", "os": "TestOS"}}
PLAN = [{"id": "lika-plus", "name": "Lika+", "price_rub": 1490, "days": 30, "discount_pct": 20, "connect": True}]
CHAT = {"model": "qwen3.8-27b", "messages": [{"role": "user", "content": "hi"}]}
CHEAP = {"model": "deepseek-v4.1-flash", "messages": [{"role": "user", "content": "hi"}]}


async def chat(client, key: str, body: dict | None = None):
    return await client.post("/v1/chat/completions", headers=auth(key), json=body or CHAT)


async def allowed_turns(client, key: str, body: dict | None = None) -> int:
    """How many turns in a row the account is allowed before the refusal: the ceiling, seen
    from the only side that matters."""
    allowed = 0
    for _ in range(100):
        r = await chat(client, key, body)
        if r.status_code != 200:
            assert r.status_code == 429 and r.json()["error"]["code"] == "allowance_exhausted"
            return allowed
        allowed += 1
    raise AssertionError("100 turns and no refusal: the ceiling is not holding")


def last_chat(cloud: Cloud, account_id: str):
    """The ledger line of the account's latest text turn: what it cost the operator and what
    the account paid for it."""
    return cloud.db._conn.execute(
        "SELECT cost_uy, sale_uy FROM ledger WHERE account_id=? AND kind='chat' ORDER BY id DESC LIMIT 1",
        (account_id,),
    ).fetchone()


def provider_turns(up) -> int:
    """How many text turns actually reached the provider: a refusal must be the relay's own
    answer, never a request that was paid for upstream and refused afterwards."""
    return len([r for r in up.state.requests if r[0] == "chat"])


def credits(cloud: Cloud, account_id: str, source: str) -> list[int]:
    rows = cloud.db._conn.execute(
        "SELECT json_extract(extra, '$.credit_uy') FROM ledger WHERE account_id=? AND kind='credit' AND json_extract(extra, '$.from')=?",
        (account_id, source),
    ).fetchall()
    return [int(r[0]) for r in rows]


# -- 1. the welcome pool and the invite bonus do not open a second lane -------------------


async def test_the_welcome_pool_is_given_once_and_spent_once():
    """A gift of ¥0.0004: one turn may go through (a chat is priced after the fact, so its
    price is known only when the answer is in), and from then on the account is refused —
    the pool never refills itself, and a second sign-in is not a second gift."""
    app, client, sender, up, cloud, settings = make(allowance_cny=0.0004)
    first = await sign_up(client, sender, "13800138000", "pixel")
    key, account_id = first["api_key"], first["account"]["id"]
    grant = int(cloud.db.account(account_id)["grant_uy"])
    assert grant == settings.allowance_uy == 400

    # the same person on a second device: a new key, the same single gift
    second = await sign_up(client, sender, "13800138000", "tablet")
    assert second["created"] is False and second["api_key"] != key
    assert int(cloud.db.account(account_id)["grant_uy"]) == grant
    assert credits(cloud, account_id, "signup") == [grant]

    r = await chat(client, key)
    assert r.status_code == 200 and provider_turns(up) == 1
    me = (await client.get("/v1/me", headers=auth(key))).json()
    assert me["spend"]["left"] == 0 and me["spend"]["total"] > 0

    # every further turn is the relay's refusal, and no gift is credited behind it
    for _ in range(3):
        r = await chat(client, key)
        assert r.status_code == 429 and r.json()["error"]["code"] == "allowance_exhausted"
    assert provider_turns(up) == 1
    assert int(cloud.db.account(account_id)["grant_uy"]) == grant
    me = (await client.get("/v1/me", headers=auth(key))).json()
    assert me["spend"]["left"] == 0  # clamped at nothing left, never a debt
    await client.aclose()


async def test_the_invite_bonus_is_paid_once_per_friend_and_only_after_a_turn():
    """A sign-up with a friend's code pays nobody: the bonus lands at the newcomer's first
    charged request, once, to both sides — and a second turn, a second sign-in and a direct
    second settlement put nothing more in either pool."""
    app, client, sender, up, cloud, settings = make(invite_bonus_cny=5.0)
    inviter = await sign_up(client, sender, "13800138000")
    inviter_key, inviter_id = inviter["api_key"], inviter["account"]["id"]
    code = (await client.get("/v1/me/invite", headers=auth(inviter_key))).json()["code"]
    base = int(cloud.db.account(inviter_id)["grant_uy"])
    bonus = settings.cny_to_uy(5.0)

    friend = await sign_up(client, sender, "13900139000", invite=code)
    friend_key, friend_id = friend["api_key"], friend["account"]["id"]
    friend_base = int(cloud.db.account(friend_id)["grant_uy"])
    # the link is written, the money is not: a farmed sign-up alone pays nobody
    assert int(cloud.db.account(inviter_id)["grant_uy"]) == base
    assert int(cloud.db.account(friend_id)["grant_uy"]) == friend_base
    assert credits(cloud, inviter_id, "invite") == [] and credits(cloud, friend_id, "invited") == []

    assert (await chat(client, friend_key)).status_code == 200
    assert int(cloud.db.account(inviter_id)["grant_uy"]) == base + bonus
    assert int(cloud.db.account(friend_id)["grant_uy"]) == friend_base + bonus
    assert credits(cloud, inviter_id, "invite") == [bonus]
    assert credits(cloud, friend_id, "invited") == [bonus]

    # more turns, a second sign-in with the same code and a direct second settlement: the
    # bonus was paid, and paying it again would be the relay printing money
    for _ in range(2):
        assert (await chat(client, friend_key)).status_code == 200
    await sign_up(client, sender, "13900139000", device="tablet", invite=code)
    assert cloud.db.settle_invite(friend_id, bonus) == (False, inviter_id, False)
    assert int(cloud.db.account(inviter_id)["grant_uy"]) == base + bonus
    assert int(cloud.db.account(friend_id)["grant_uy"]) == friend_base + bonus
    assert credits(cloud, inviter_id, "invite") == [bonus]
    assert cloud.db.account(inviter_id)["invites"] == 1

    # a second friend is a second bonus, and still one line per friend
    other = await sign_up(client, sender, "13700137000", invite=code)
    assert (await chat(client, other["api_key"])).status_code == 200
    assert int(cloud.db.account(inviter_id)["grant_uy"]) == base + 2 * bonus
    assert credits(cloud, inviter_id, "invite") == [bonus, bonus]
    await client.aclose()


async def test_the_invite_bonus_is_spent_like_any_other_money():
    """The bonus is money in the same pool, not a lane of its own: the gift buys as many
    turns as it costs, the friend's first turn puts the bonus in, that many turns more fit,
    and the same ceiling stops the account again."""
    app, client, sender, up, cloud, settings = make(allowance_cny=0.002, invite_bonus_cny=0.002)
    inviter = await sign_up(client, sender, "13800138000")
    inviter_key, inviter_id = inviter["api_key"], inviter["account"]["id"]
    code = (await client.get("/v1/me/invite", headers=auth(inviter_key))).json()["code"]

    # what the gift buys, and no turn beyond it
    assert (await chat(client, inviter_key, CHEAP)).status_code == 200
    per_turn = cloud.db.spent_since(inviter_id, 0)
    assert per_turn > 0
    alone = 1 + await allowed_turns(client, inviter_key, CHEAP)
    gift = int(cloud.db.account(inviter_id)["grant_uy"])
    assert alone == math.ceil(gift / per_turn)
    assert cloud.db.spent_since(inviter_id, 0) > gift  # the last turn may pass the line …

    friend = await sign_up(client, sender, "13900139000", invite=code)
    assert (await chat(client, friend["api_key"], CHEAP)).status_code == 200
    pool = int(cloud.db.account(inviter_id)["grant_uy"])
    assert pool == gift + settings.cny_to_uy(0.002)
    # one subtraction for both: the bonus sits in the same pool as the gift
    left = cloud.allowance(cloud.authenticate(inviter_key))["left_uy"]
    assert left == pool - cloud.db.spent_since(inviter_id, 0)

    # … and the bonus buys the turns its money covers, counted the same way
    with_bonus = await allowed_turns(client, inviter_key, CHEAP)
    assert with_bonus == math.ceil(left / per_turn)
    r = await chat(client, inviter_key, CHEAP)
    assert r.status_code == 429 and r.json()["error"]["code"] == "allowance_exhausted"
    me = (await client.get("/v1/me", headers=auth(inviter_key))).json()
    assert me["spend"]["left"] == 0
    await client.aclose()


# -- 2. nothing left means a refusal ------------------------------------------------------


async def test_an_account_at_zero_is_refused_and_never_goes_below():
    """With the token ceiling on, the last token is the last turn: `402 out_of_tokens` for
    the next one, and a ledger already past its ceiling (an older relay, a correction) is
    refused the same way instead of being allowed to dig deeper."""
    app, client, sender, up, cloud, settings = make(signup_tokens=10)
    data = await sign_up(client, sender, "13800138000")
    key, account_id = data["api_key"], data["account"]["id"]
    assert (await chat(client, key)).status_code == 200 and provider_turns(up) == 1

    r = await chat(client, key)
    assert r.status_code == 402 and r.json()["error"]["code"] == "out_of_tokens"
    assert provider_turns(up) == 1  # nothing was asked of the provider for a turn that was refused

    cloud.db.charge(account_id, "chat", "qwen3.8-27b", 10, 10, 5000, "over-the-top")
    r = await chat(client, key)
    assert r.status_code == 402 and r.json()["error"]["code"] == "out_of_tokens"
    assert provider_turns(up) == 1
    me = (await client.get("/v1/me", headers=auth(key))).json()
    assert me["tokens"]["remaining"] == 0  # a floor, not a negative balance
    await client.aclose()


async def test_the_daily_share_is_a_refusal_and_not_an_overdraft():
    """The day's free share runs out at its figure: the next turns are `429 daily_cap`, the
    provider is not called, and nothing is quietly taken beyond the share."""
    app, client, sender, up, cloud, settings = make(daily_cap_tokens=60)
    data = await sign_up(client, sender, "13800138000")
    key = data["api_key"]
    assert data["tokens"]["daily_cap"] == 60
    assert (await chat(client, key)).status_code == 200
    spent_after_first = (await client.get("/v1/me", headers=auth(key))).json()["spend"]["total"]

    for _ in range(3):
        r = await chat(client, key)
        assert r.status_code == 429 and r.json()["error"]["code"] == "daily_cap"
    assert provider_turns(up) == 1
    me = (await client.get("/v1/me", headers=auth(key))).json()
    assert me["spend"]["total"] == spent_after_first
    assert me["tokens"]["used_today"] >= 60
    await client.aclose()


# -- 3. lapsed terms, checked by the server from its own clock ----------------------------


async def test_a_lapsed_tariff_is_read_by_the_relay_not_by_the_client(monkeypatch):
    """A tariff that ran out yesterday takes its discount with it: the relay compares the
    date it stored with its own clock, so no device has to say so and no device can claim
    otherwise."""
    app, client, sender, up, cloud, settings = make(rub_per_usd=90, connect_requires_plan=True, plans_json=json.dumps(PLAN))
    data = await sign_up(client, sender, "13800138000")
    key, account_id = data["api_key"], data["account"]["id"]
    assert cloud.connect_state(account_id)["allowed"] is False

    cloud.plan_buy(account_id, "lika-plus")
    assert cloud.connect_state(account_id)["allowed"] is True
    assert cloud.spend_discount_pct(account_id) == 20
    me = (await client.get("/v1/me", headers=auth(key))).json()
    assert me["plan"]["active"] is True and me["plan"]["connect"] is True
    assert (await chat(client, key)).status_code == 200
    # what the account pays for the turn carries the tariff's share off, and it is the
    # relay's own reading of the stored date that says so, not anything the client sent
    line = last_chat(cloud, account_id)
    assert line["cost_uy"] == 900 and line["sale_uy"] == 720

    # a month and a day later, with the stored terms untouched
    clock = cloud.clock()
    monkeypatch.setattr(Cloud, "clock", staticmethod(lambda: clock + 31 * 86400))
    state = cloud.plan_state(account_id)
    assert state is not None and state["active"] is False
    assert cloud.spend_discount_pct(account_id) == 0
    assert cloud.discount(10000, account_id) == 10000  # the full price, the tariff is over
    assert cloud.connect_state(account_id)["allowed"] is False
    assert (await client.get("/v1/me", headers=auth(key))).json()["plan"]["active"] is False

    # the same turn now costs the full price: the lapsed terms took nothing off
    assert (await chat(client, key)).status_code == 200
    expired = last_chat(cloud, account_id)
    assert expired["cost_uy"] == line["cost_uy"] and expired["sale_uy"] == expired["cost_uy"]
    await client.aclose()


def test_the_hub_closes_a_connection_whose_tariff_has_lapsed():
    """The connection itself is checked when the socket opens, not when the app says it is
    paid for: the same key that met through the hub yesterday is refused today."""
    from test_plans import sign_up as ws_sign_up
    from test_plans import stack as ws_stack

    client, cloud, sender = ws_stack(connect_requires_plan=True)
    key, me = ws_sign_up(client, sender)
    account_id = me["account"]["id"]
    clock = cloud.clock()
    cloud.plan_buy(account_id, "lika-plus")
    with client.websocket_connect("/v1/hub", headers={"Authorization": f"Bearer {key}"}) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["type"] == "welcome"

    cloud.clock = staticmethod(lambda: clock + 31 * 86400)
    assert cloud.connect_allowed(account_id) is False
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect("/v1/hub", headers={"Authorization": f"Bearer {key}"}) as ws:
            ws.send_json(HELLO)
            frame = ws.receive_json()
            assert frame["code"] == "connect_limited"
            ws.receive_json()  # the close that follows the frame
    assert refused.value.code == 4004


# -- 4. one account sees nothing of another ----------------------------------------------


async def test_one_account_sees_nothing_of_another():
    """Two keys, two people: the second one reads no conversation of the first, deletes
    none, sees no device of it, cannot revoke one, and its account card carries its own
    balance and nobody else's spend."""
    app, client, sender, up, cloud, settings = make()
    mine = await sign_up(client, sender, "13800138000")
    their = await sign_up(client, sender, "13900139000")
    my_key, my_id = mine["api_key"], mine["account"]["id"]
    their_key, their_id = their["api_key"], their["account"]["id"]
    assert my_id != their_id

    cid = "8a4a1f5a-1111-4a3a-9a4a-000000000001"
    push = await client.post(
        "/v1/sync/changes",
        headers=auth(my_key),
        json={
            "device": "phone-1",
            "conversations": [{"cid": cid, "kind": "main", "title": "Mine", "created_at": 1738000000}],
            "messages": [],
        },
    )
    assert push.status_code == 200, push.text
    cloud.db.upsert_device(my_id, "phone-1", "Pixel", "phone", "Android", "1.0", "[]")
    assert (await chat(client, my_key)).status_code == 200

    # the chats: not in the list, not deletable, and untouched by the attempt
    assert (await client.get("/v1/sync/conversations", headers=auth(their_key))).json()["conversations"] == []
    gone = await client.delete(f"/v1/sync/conversations/{cid}", headers=auth(their_key))
    assert gone.status_code == 404 and gone.json()["error"]["code"] == "no_conversation"
    row = await client.get("/v1/sync/conversations", headers=auth(my_key))
    assert [c["cid"] for c in row.json()["conversations"]] == [cid]

    # the devices: an empty list, and a revoke that reaches nothing
    assert (await client.get("/v1/devices", headers=auth(their_key))).json()["devices"] == []
    assert [d["id"] for d in (await client.get("/v1/devices", headers=auth(my_key))).json()["devices"]] == ["phone-1"]
    revoked = await client.delete("/v1/devices/phone-1", headers=auth(their_key))
    assert revoked.status_code == 204
    assert [d["id"] for d in (await client.get("/v1/devices", headers=auth(my_key))).json()["devices"]] == ["phone-1"]

    # the card: one's own account and one's own spend
    my_me = (await client.get("/v1/me", headers=auth(my_key))).json()
    their_me = (await client.get("/v1/me", headers=auth(their_key))).json()
    assert my_me["account"]["id"] == my_id and their_me["account"]["id"] == their_id
    assert their_me["account"]["hint"] == "139****9000" and my_me["account"]["hint"] == "138****8000"
    assert my_me["spend"]["total"] > 0 and their_me["spend"]["total"] == 0
    assert their_me["spend"]["left"] == their_me["spend"]["grant"]
    await client.aclose()


# -- 5. the operator's routes, and the account's own ------------------------------------


async def test_every_operator_route_needs_the_operator_token():
    """Walk the app's own table of routes: nothing under `/v1/admin` answers without the
    operator's token, and the token is what opens them — including the newer prices page the
    console reads."""
    app, client, sender, up, cloud, settings = make()
    paths = {}
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/v1/admin"):
            continue
        for method in getattr(route, "methods", set()) or set():
            if method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                paths[(method, _filled(path))] = path
    assert paths, "the app has operator routes"
    assert "/v1/admin/prices" in paths.values()

    for (method, url), _path in paths.items():
        plain = await client.request(method, url)
        assert plain.status_code == 401, (method, url)
        assert plain.json()["error"]["code"] == "admin"
        wrong = await client.request(method, url, headers={"X-Admin-Token": "not-the-token"})
        assert wrong.status_code == 401, (method, url)

    assert (await client.get("/v1/admin/prices", headers=ADMIN_HEADERS)).status_code == 200
    await client.aclose()


async def test_the_link_routes_need_a_key_and_the_bot_door_its_own_token():
    """`/v1/link/*` are the account's own routes, not the operator's: an operator's token
    opens nothing there, and the bot's two doors answer only to the bot's shared secret."""
    app, client, sender, up, cloud, settings = make(bot_signin_token=BOT)
    for headers in ({}, ADMIN_HEADERS):
        r = await client.post("/v1/link/code", headers=headers)
        assert r.status_code == 401 and r.json()["error"]["code"] == "bad_key"
        r = await client.get("/v1/link/channels", headers=headers)
        assert r.status_code == 401 and r.json()["error"]["code"] == "bad_key"

    data = await sign_up(client, sender, "13800138000")
    for headers in ({}, ADMIN_HEADERS):
        r = await client.post("/v1/bot/link", json={"chat_id": "5188775031", "code": "ZZZZZZ"}, headers=headers)
        assert r.status_code == 401 and r.json()["error"]["code"] == "bad_bot_token"
        r = await client.get("/v1/bot/link", params={"chat_id": "5188775031"}, headers=headers)
        assert r.status_code == 401

    # the account's own key still asks for its code, and the bot's secret still links
    assert (await client.post("/v1/link/code", headers=auth(data["api_key"]))).status_code == 200
    await client.aclose()


# -- 6. few tries, not a guessing machine ------------------------------------------------


async def test_a_sign_in_code_takes_a_few_wrong_tries_and_no_more():
    """Three guesses burn the code: after them the right six digits are refused as expired,
    which is what makes guessing pointless — a fresh code is a fresh three tries."""
    app, client, sender, up, cloud, settings = make(code_max_attempts=3)
    ident = "13800138000"
    assert (await client.post("/v1/auth/code", json={"identifier": ident})).status_code == 204
    _, code = sender.sent[-1]

    wrong = []
    for guess in ("000000", "111111", "222222"):
        r = await client.post("/v1/auth/verify", json={"identifier": ident, "code": guess})
        wrong.append(r.json()["error"]["code"])
        assert r.status_code == 400
    assert wrong == ["code_wrong", "code_wrong", "code_wrong"]

    r = await client.post("/v1/auth/verify", json={"identifier": ident, "code": code, "device": "pixel"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "code_expired"

    assert (await client.post("/v1/auth/code", json={"identifier": ident})).status_code == 204
    _, fresh = sender.sent[-1]
    r = await client.post("/v1/auth/verify", json={"identifier": ident, "code": fresh, "device": "pixel"})
    assert r.status_code == 200, r.text
    await client.aclose()


async def test_a_link_code_takes_a_few_wrong_tries_and_no_more(monkeypatch):
    """The six characters a chat types are counted per chat: past the count the door answers
    `429 link_limited` to everything, a right code included, so a bot relaying a stranger's
    guesses learns nothing — and the window is where it opens again."""
    app, client, sender, up, cloud, settings = make(bot_signin_token=BOT, link_max_attempts=3)
    data = await sign_up(client, sender, "13800138000")
    key = data["api_key"]
    code = (await client.post("/v1/link/code", headers=auth(key))).json()["code"]
    chat_id = "5188775031"

    async def guess(value: str, who: str = chat_id):
        return await client.post("/v1/bot/link", json={"chat_id": who, "code": value}, headers=BOT_HEADERS)

    for _ in range(3):
        r = await guess("ZZZZZZ")
        assert r.status_code == 400 and r.json()["error"]["code"] == "link_wrong"
    r = await guess("ZZZZZZ")
    assert r.status_code == 429 and r.json()["error"]["code"] == "link_limited"
    # the right one is refused the same way: the door is shut, not telling
    r = await guess(code)
    assert r.status_code == 429 and r.json()["error"]["code"] == "link_limited"
    assert r.json()["error"]["message"] == (await guess(code)).json()["error"]["message"]

    # another chat is counted on its own and links as before
    assert (await guess(code, "7007007007")).status_code == 200

    # the window passes: a fresh code from the app works in a chat that was shut
    clock = {"t": db_now()}
    monkeypatch.setattr("nanomuse_cloud.db.now", lambda: clock["t"])
    clock["t"] += cloud.link_attack_window_s() + 1
    fresh = (await client.post("/v1/link/code", headers=auth(key))).json()["code"]
    assert (await guess(fresh)).status_code == 200
    # a code that worked starts the count over: no leftover failures for this chat
    rows = cloud.db._conn.execute(
        "SELECT COUNT(*) FROM link_attempts WHERE ident_hash=?", (telegram(chat_id).hash(cloud.s.hmac_key),)
    ).fetchone()
    assert int(rows[0]) == 0
    await client.aclose()


async def test_the_apps_door_counts_its_link_tries_too():
    """The other end of the same six characters: an address typing link codes beside its own
    sign-in code is counted as well, so a mailbox one owns is not a free oracle."""
    app, client, sender, up, cloud, settings = make(
        bot_signin_token=BOT, link_max_attempts=3, code_per_identifier_10m=50, code_per_ip_hour=500
    )
    account = (await client.post("/v1/bot/signin", json={"telegram_id": "5188775031"}, headers=BOT_HEADERS)).json()
    code = (await client.post("/v1/link/code", headers=auth(account["api_key"]))).json()["code"]
    address = "joins@example.com"

    async def try_link(value: str):
        assert (await client.post("/v1/auth/code", json={"identifier": address})).status_code == 204
        _, own = sender.sent[-1]
        return await client.post("/v1/auth/verify", json={"identifier": address, "code": own, "link": value, "device": "desk"})

    for _ in range(3):
        r = await try_link("ZZZZZZ")
        assert r.status_code == 400 and r.json()["error"]["code"] == "link_wrong"
    r = await try_link(code)
    assert r.status_code == 429 and r.json()["error"]["code"] == "link_limited"
    await client.aclose()


def _filled(path: str) -> str:
    """A path from the route table with its parameters made concrete, so the route can be
    asked without a schema: what is tested is the door, not the body."""
    for name in ("{account_id}", "{metric_id}", "{rule_id}", "{key}", "{device_id}"):
        path = path.replace(name, "x")
    return path
