"""Tariffs: what a purchase puts into the pool, what it takes off a spend, what it opens."""

from __future__ import annotations

import json

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
    assert menu["lika"]["price_rub"] == 590 and menu["lika"]["discount_pct"] == 10
    assert menu["lika"]["connect"] is True
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
    assert cloud.discount(1000, lika) == 900
    assert cloud.discount(1000, team) == 700
    # a spend smaller than a hundred is still a spend
    assert cloud.discount(3, lika) == 3


def test_every_site_tariff_opens_the_connection():
    client, cloud, sender = stack(connect_requires_plan=True)
    key, me = sign_up(client, sender)
    account_id = me["account"]["id"]
    assert cloud.connect_allowed(account_id) is False
    # The public site promises device connection with every paid package.
    cloud.plan_buy(account_id, "lika")
    assert cloud.connect_allowed(account_id) is True
    state = cloud.connect_state(account_id)
    assert state["open_from_rub"] == 590
    cloud.plan_buy(account_id, "team")
    assert cloud.connect_allowed(account_id) is True


def test_without_the_switch_every_account_connects_as_before():
    client, cloud, sender = stack(connect_requires_plan=False)
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
            assert frame["code"] == "connect_limited" and frame["open_from_rub"] == 590
            ws.receive_json()
    assert refused.value.code == 4004 and refused.value.reason == "connect_limited"


def test_a_tariff_that_opens_it_lets_the_devices_meet():
    client, cloud, sender = stack(connect_requires_plan=True)
    key, me = sign_up(client, sender)
    cloud.plan_buy(me["account"]["id"], "lika-plus")
    with client.websocket_connect("/v1/hub", headers={"Authorization": f"Bearer {key}"}) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["type"] == "welcome"


def test_expired_tariff_keeps_balance_but_removes_discount_and_required_connection():
    _client, cloud, _sender = stack(connect_requires_plan=True)
    account_id = cloud.db.create_account("x" * 64, "email", "x***@example.com", 0)["id"]
    cloud.plan_buy(account_id, "lika")
    granted = int(cloud.db.account(account_id)["grant_uy"])
    with cloud.db.tx() as conn:
        conn.execute("UPDATE subscriptions SET expires_at=? WHERE account_id=?", (cloud.clock() - 1, account_id))

    state = cloud.plan_state(account_id)
    assert state is not None and state["active"] is False
    assert cloud.spend_discount_pct(account_id) == 0
    assert cloud.db.account(account_id)["grant_uy"] == granted
    assert cloud.connect_allowed(account_id) is False


def test_public_config_reports_lifetime_and_daily_free_grants_separately():
    settings = Settings(database=":memory:", secret="test-secret", daily_free_cny=0.25, start_free_cny=5)
    cloud = Cloud(settings, Database(":memory:"), LogSender())
    config = cloud.public_config()
    assert config["daily_free_cny"] == 0.25
    assert config["start_free_cny"] == 5
    assert config["plans"][0]["discount_pct"] == 10
    assert config["plans"][0]["connect"] is True


def test_daily_free_slice_is_credited_once_atomically_per_utc_day():
    db = Database(":memory:")
    account_id = db.create_account("d" * 64, "email", "d***@example.com", 0)["id"]
    grant = 1_250_000
    assert db.daily_grant_once(account_id, grant, ts=1_800_000_000) is True
    assert db.daily_grant_once(account_id, grant, ts=1_800_000_100) is False
    assert int(db.account(account_id)["grant_uy"]) == grant
    assert db.daily_grant_once(account_id, grant, ts=1_800_086_400) is True
    assert int(db.account(account_id)["grant_uy"]) == grant * 2


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

def test_the_admin_card_shows_the_tariff_and_the_connection():
    """Карточка аккаунта в админке отдаёт тариф и состояние связи: по ним оператор
    видит, что человек купил, и может продать следующий тариф не выходя из консоли."""
    client, _cloud, sender = stack()
    _key, me = sign_up(client, sender)
    account_id = me["account"]["id"]
    before = client.get(f"/v1/admin/accounts/{account_id}", headers=ADMIN).json()["account"]
    assert before["plan"] is None
    client.post("/v1/admin/plan", json={"account_id": account_id, "plan": "lika-plus"}, headers=ADMIN)
    after = client.get(f"/v1/admin/accounts/{account_id}", headers=ADMIN).json()["account"]
    assert after["plan"]["id"] == "lika-plus" and after["plan"]["active"] is True
    assert after["plan"]["expires_at"] > 0
    assert after["connect"]["allowed"] is True
    assert after["connect"]["plan"]["id"] == "lika-plus"


def _plans() -> str:
    return json.dumps(
        [
            {
                "id": "lika",
                "name": "Лика",
                "price_rub": 590,
                "discount_pct": 10,
                "connect": True,
                "max_chat_out_rub": 300,
                "max_video_tokens": 300_000,
                "video_min_paid_rub": 590,
            },
            {
                "id": "lika-plus",
                "name": "Лика+",
                "price_rub": 1490,
                "discount_pct": 20,
                "connect": True,
                "video_min_paid_rub": 590,
            },
        ]
    )


def test_a_low_tariff_hides_an_expensive_chat_model_and_a_higher_one_opens_it():
    client, cloud, sender = stack(plans_json=_plans())
    key, me = sign_up(client, sender)
    account_id = me["account"]["id"]
    headers = {"Authorization": f"Bearer {key}"}

    free = {m["id"] for m in client.get("/v1/me/models", headers=headers).json()["models"]}
    assert "qwen3.8-flash" in free
    assert "deepseek-v4.1-flash" not in free  # sell_out ₽1040 > the low plan's ₽300 cap

    cloud.plan_buy(account_id, "lika")
    low = {m["id"] for m in client.get("/v1/me/models", headers=headers).json()["models"]}
    assert "deepseek-v4.1-flash" not in low

    cloud.plan_buy(account_id, "lika-plus")
    high = {m["id"] for m in client.get("/v1/me/models", headers=headers).json()["models"]}
    assert "deepseek-v4.1-flash" in high


def test_a_video_model_opens_only_when_paid_money_covers_the_provider_deposit():
    client, cloud, sender = stack(plans_json=_plans())
    key, me = sign_up(client, sender)
    account_id = me["account"]["id"]
    cloud.plan_buy(account_id, "lika")
    caller = cloud.authenticate(key)

    with pytest.raises(CloudError) as refused:
        cloud.model_for("wan2.2-i2v-flash", "video", caller)
    assert refused.value.code == "video_reserve"

    # A tariff month opened at ₽590 must not close video after a ₽10 spend: the
    # provider's ₽500 reserve is still covered.
    with cloud.db.tx() as conn:
        conn.execute(
            "UPDATE accounts SET paid_balance_uy=? WHERE id=?",
            (cloud.s.rub_to_uy(580), account_id),
        )
    assert cloud.model_for("wan2.2-i2v-flash", "video", caller).id == "wan2.2-i2v-flash"

    with cloud.db.tx() as conn:
        conn.execute(
            "UPDATE accounts SET paid_balance_uy=? WHERE id=?",
            (cloud.s.rub_to_uy(590), account_id),
        )
    assert cloud.model_for("wan2.2-i2v-flash", "video", caller).id == "wan2.2-i2v-flash"
