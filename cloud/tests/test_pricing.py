"""What a turn costs the person and what it costs the operator: separate figures."""

from __future__ import annotations

import pytest

from nanomuse_cloud.config import Settings
from nanomuse_cloud.db import Database
from nanomuse_cloud.senders import LogSender
from nanomuse_cloud.service import Caller, Cloud

MILLION = 1_000_000


def stack(**overrides) -> Cloud:
    kw = dict(database=":memory:", secret="test-secret", rub_per_usd=100, topup_fee_pct=6, signup_tokens=1000)
    kw.update(overrides)
    return Cloud(Settings(**kw), Database(":memory:"), LogSender())


def account_of(cloud: Cloud, tail: str = "a") -> Caller:
    row = cloud.db.create_account(tail * 64, "email", f"{tail}***@example.com", 0)
    return Caller(
        key_hash=tail * 16,
        account_id=str(row["id"]),
        channel="email",
        hint=f"{tail}***",
        granted=0,
        used=0,
        account_created_at=0,
    )


def lines(cloud: Cloud, account_id: str) -> list[tuple[int, int]]:
    return [
        (int(r["cost_uy"]), int(r["sale_uy"]))
        for r in cloud.db._conn.execute("SELECT cost_uy, sale_uy FROM ledger WHERE account_id=? ORDER BY id", (account_id,)).fetchall()
    ]


def test_a_text_turn_is_sold_at_the_approved_rate_not_at_the_providers_price():
    cloud = stack()
    caller = account_of(cloud)
    model = cloud.s.model("deepseek-v4.1-flash")
    assert model is not None
    cloud.charge_chat(caller, model, MILLION, 0, "r1")
    cost, sale = lines(cloud, caller.account_id)[-1]
    # the provider bills ¥2 a million fresh input; the approved rate is ₽260 a million
    assert cost == 2_000_000
    assert sale == cloud.s.rub_to_uy(260)
    assert sale > cost, "the relay must sell above what it pays"


def test_cached_input_has_its_own_rate():
    cloud = stack()
    caller = account_of(cloud)
    model = cloud.s.model("deepseek-v4.1-flash")
    assert model is not None
    cloud.charge_chat(caller, model, MILLION, 0, "r1", cached_tokens=MILLION // 2)
    _cost, sale = lines(cloud, caller.account_id)[-1]
    # half a million fresh at ₽260 and half a million cached at ₽6
    assert sale == cloud.s.rub_to_uy(130 + 3)


def test_a_running_tariff_takes_its_share_off_the_money():
    cloud = stack()
    caller = account_of(cloud)
    model = cloud.s.model("deepseek-v4.1-flash")
    assert model is not None
    cloud.charge_chat(caller, model, MILLION, 0, "r1")
    plain = lines(cloud, caller.account_id)[-1][1]
    cloud.plan_buy(caller.account_id, "lika")
    cloud.charge_chat(caller, model, MILLION, 0, "r2")
    with_plan = lines(cloud, caller.account_id)[-1][1]
    assert with_plan == pytest.approx(plain * 0.8, rel=0.001)
    cloud.plan_buy(caller.account_id, "team")
    cloud.charge_chat(caller, model, MILLION, 0, "r3")
    with_team = lines(cloud, caller.account_id)[-1][1]
    assert with_team == pytest.approx(plain * 0.7, rel=0.001)


def test_the_tariff_never_touches_pictures():
    cloud = stack()
    caller = account_of(cloud)
    cloud.plan_buy(caller.account_id, "team")
    model = cloud.s.model("qwen-image-3.0")
    assert model is not None
    cloud.charge_image(caller, model, 1, "r1")
    cost, sale = lines(cloud, caller.account_id)[-1]
    assert cost == sale, "the approved discount is for text turns; a picture is sold at its price"


def test_a_model_without_a_sale_rate_is_charged_the_providers_price():
    cloud = stack()
    caller = account_of(cloud)
    model = cloud.s.model("qwen3.8-27b")
    assert model is not None and model.sells_at_own_rate() is False
    cloud.charge_chat(caller, model, MILLION, 0, "r1")
    cost, sale = lines(cloud, caller.account_id)[-1]
    assert cost == sale


def test_a_relay_without_a_ruble_rate_sells_at_the_providers_price():
    cloud = stack(rub_per_usd=0)
    caller = account_of(cloud)
    model = cloud.s.model("deepseek-v4.1-flash")
    assert model is not None and model.sells_at_own_rate() is True
    cloud.charge_chat(caller, model, MILLION, 0, "r1")
    cost, sale = lines(cloud, caller.account_id)[-1]
    assert cost == sale, "no rate to convert rubles with: charge what the provider charges"


def spent(cloud: Cloud, account_id: str) -> int:
    """What the account has spent, as the relay counts it: the sale figures of the ledger."""
    return int(
        cloud.db._conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN cost_uy>0 THEN CASE WHEN sale_uy>0 THEN sale_uy ELSE cost_uy END ELSE 0 END),0)"
            " FROM ledger WHERE account_id=?",
            (account_id,),
        ).fetchone()[0]
    )


def test_the_pool_falls_by_what_was_sold():
    cloud = stack()
    caller = account_of(cloud)
    model = cloud.s.model("deepseek-v4.1-flash")
    assert model is not None
    grant = int(cloud.db.account(caller.account_id)["grant_uy"])
    cloud.charge_chat(caller, model, MILLION, 0, "r1")
    sale = lines(cloud, caller.account_id)[-1][1]
    assert spent(cloud, caller.account_id) == sale
    # the pool is the grant less what the ledger says the account paid
    assert grant - spent(cloud, caller.account_id) == grant - sale
    assert cloud.connect_state(caller.account_id) is not None


def test_the_sale_rate_of_one_model_is_not_another_models():
    cloud = stack()
    flash = cloud.s.model("deepseek-v4.1-flash")
    qwen = cloud.s.model("qwen3.8-flash")
    assert flash is not None and qwen is not None
    assert flash.sells_at_own_rate() is True
    assert qwen.sells_at_own_rate() is False
