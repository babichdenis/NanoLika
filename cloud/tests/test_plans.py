"""Tariffs: what a purchase puts into the pool, what it takes off a spend, what it opens."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from nanomuse_cloud.api import create_app
from nanomuse_cloud.config import Settings
from nanomuse_cloud.db import Database
from nanomuse_cloud.senders import LogSender
from nanomuse_cloud.service import Cloud, CloudError

ADMIN = {"X-Admin-Token": "admin"}

HELLO = {"type": "hello", "device": {"id": "pc-1", "name": "desk", "kind": "computer", "os": "TestOS"}}


def stack(**overrides) -> tuple[TestClient, Cloud, LogSender]:
    kw = dict(
        database=":memory:",
        secret="test-secret",
        admin_token="admin",
        public_base="http://cloud.test",
        rub_per_usd=90,
        topup_fee_pct=6,
        signup_tokens=1000,
    )
    kw.update(overrides)
    settings = Settings(**kw)
    sender = LogSender()
    cloud = Cloud(settings, Database(":memory:"), sender)
    return TestClient(create_app(settings, cloud)), cloud, sender


def sign_up(client: TestClient, sender: LogSender, identifier: str = "13800138000") -> tuple[str, dict]:
    assert client.post("/v1/auth/code", json={"identifier": identifier}).status_code == 204
    _, code = sender.sent[-1]
    r = client.post("/v1/auth/verify", json={"identifier": identifier, "code": code, "device": "test"})
    assert r.status_code == 200, r.text
    return r.json()["api_key"], r.json()


def test_the_menu_is_the_operators_three_tariffs(monkeypatch=None):
    _client, cloud, _sender = stack()
    menu = {row["id"]: row for row in cloud.plan_menu()}
    assert list(menu) == ["lika", "lika-plus", "team"]
    assert menu["lika"]["price_rub"] == 590 and menu["lika"]["discount_pct"] == 20
    assert menu["lika"]["connect"] is False
    assert menu["lika-plus"]["price_rub"] == 1490 and menu["lika-plus"]["connect"] is True
    assert menu["team"]["price_rub"] == 3900 and menu["team"]["discount_pct"] == 30
    assert all(row["days"] == 30 for row in menu.values())


def test_a_tariff_puts_its_whole_price_into_the_pool():
    _client, cloud, _sender = stack()
    account_id = cloud.db.create_account("h" * 64, "email", "a***@example.com", 0)["id"]
    before = int(cloud.db.account(account_id)["grant_uy"])
    sold = cloud.plan_buy(account_id, "lika")
    after = int(cloud.db.account(account_id)["grant_uy"])
    assert sold["credited_rub"] == 590
    assert after - before == cloud.s.rub_to_uy(590)
    state = cloud.plan_state(account_id)
    assert state is not None and state["active"] is True and state["id"] == "lika"
    # the suite pins Cloud.clock to a busy hour, so the month is measured against that clock
    assert state["expires_at"] - cloud.clock() == 30 * 86400


def test_buying_again_extends_the_month_instead_of_starting_over():
    _client, cloud, _sender = stack()
    account_id = cloud.db.create_account("h" * 64, "email", "a***@example.com", 0)["id"]
    cloud.plan_buy(account_id, "lika")
    first = cloud.plan_state(account_id)
    cloud.plan_buy(account_id, "lika")
    second = cloud.plan_state(account_id)
    assert first and second
    assert second["expires_at"] - first["expires_at"] == 30 * 86400
    assert second["started_at"] == first["started_at"]


def test_the_discount_is_what_the_tariff_says():
    _client, cloud, _sender = stack()
    plain = cloud.db.create_account("a" * 64, "email", "a***@example.com", 0)["id"]
    lika = cloud.db.create_account("b" * 64, "email", "b***@example.com", 0)["id"]
    team = cloud.db.create_account("c" * 64, "email", "c***@example.com", 0)["id"]
    cloud.plan_buy(lika, "lika")
    cloud.plan_buy(team, "team")
    assert cloud.discount(1000, plain) == 1000
    assert cloud.discount(1000, lika) == 800
    assert cloud.discount(1000, team) == 700
    # a spend smaller than a hundred is still a spend
    assert cloud.discount(3, lika) == 3


def test_a_tariff_that_does_not_open_the_connection_is_not_a_key():
    client, cloud, sender = stack(connect_requires_plan=True)
    key, me = sign_up(client, sender)
    account_id = me["account"]["id"]
    assert cloud.connect_allowed(account_id) is False
    # Лика gives the discount and the balance, not the relay's connection
    cloud.plan_buy(account_id, "lika")
    assert cloud.connect_allowed(account_id) is False
    # Лика+ opens it
    cloud.plan_buy(account_id, "lika-plus")
    assert cloud.connect_allowed(account_id) is True
    state = cloud.connect_state(account_id)
    assert state["open_from_rub"] == 1490


def test_without_the_switch_every_account_connects_as_before():
    client, cloud, sender = stack()
    _key, me = sign_up(client, sender)
    assert cloud.connect_allowed(me["account"]["id"]) is True
    assert cloud.connect_state(me["account"]["id"])["required"] is False


def test_the_hub_refuses_a_connection_only_when_the_tariff_is_the_key():
    client, _cloud, sender = stack(connect_requires_plan=True)
    key, _me = sign_up(client, sender)
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect("/v1/hub", headers={"Authorization": f"Bearer {key}"}) as ws:
            ws.send_json(HELLO)
            frame = ws.receive_json()
            assert frame["code"] == "connect_limited" and frame["open_from_rub"] == 1490
            ws.receive_json()
    assert refused.value.code == 4004 and refused.value.reason == "connect_limited"


def test_a_tariff_that_opens_it_lets_the_devices_meet():
    client, cloud, sender = stack(connect_requires_plan=True)
    key, me = sign_up(client, sender)
    cloud.plan_buy(me["account"]["id"], "lika-plus")
    with client.websocket_connect("/v1/hub", headers={"Authorization": f"Bearer {key}"}) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["type"] == "welcome"


def test_the_account_page_carries_the_tariff_and_the_connection():
    client, cloud, sender = stack(connect_requires_plan=True)
    key, me = sign_up(client, sender)
    headers = {"Authorization": f"Bearer {key}"}
    plain = client.get("/v1/me", headers=headers).json()
    assert plain["plan"] is None and plain["connect"]["allowed"] is False
    cloud.plan_buy(me["account"]["id"], "lika-plus")
    after = client.get("/v1/me", headers=headers).json()
    assert after["plan"]["id"] == "lika-plus" and after["plan"]["active"] is True
    assert after["connect"]["allowed"] is True


def test_an_unknown_tariff_is_refused_and_a_relay_without_rubles_cannot_sell_one():
    _client, cloud, _sender = stack()
    account_id = cloud.db.create_account("h" * 64, "email", "a***@example.com", 0)["id"]
    with pytest.raises(CloudError) as wrong:
        cloud.plan_buy(account_id, "gold")
    assert wrong.value.code == "no_plan"
    # without a ruble rate the price cannot become a pool figure
    _c2, cloud2, _s2 = stack(rub_per_usd=0)
    account2 = cloud2.db.create_account("i" * 64, "email", "i***@example.com", 0)["id"]
    with pytest.raises(CloudError) as no_rate:
        cloud2.plan_buy(account2, "lika")
    assert no_rate.value.code == "no_rate"


def test_the_operator_sells_a_tariff_through_the_admin_door():
    client, _cloud, sender = stack()
    _key, me = sign_up(client, sender)
    r = client.post("/v1/admin/plan", json={"account_id": me["account"]["id"], "plan": "lika-plus"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json()["plan"]["id"] == "lika-plus"
    assert client.post("/v1/admin/plan", json={"account_id": me["account"]["id"], "plan": "gold"}, headers=ADMIN).status_code == 400
