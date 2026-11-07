"""BYOK: a person's own provider key — stored encrypted, never returned, never billed."""

from __future__ import annotations

import json

from nanomuse_cloud.config import ModelSpec, Settings
from nanomuse_cloud.db import Database
from nanomuse_cloud.senders import LogSender
from nanomuse_cloud.service import Caller, Cloud


def make_cloud() -> tuple[Cloud, Caller]:
    settings = Settings(
        database=":memory:",
        secret="test-secret",
        admin_token="admin",
        upstream_base="http://upstream/compat/v1",
        upstream_key="sk-upstream",
        public_base="http://cloud.test",
    )
    cloud = Cloud(settings, Database(":memory:"), LogSender())
    caller = Caller(
        key_hash="key-hash",
        account_id="acct",
        channel="email",
        hint="a***@example.com",
        granted=0,
        used=0,
        account_created_at=0,
    )
    return cloud, caller


def add_account(cloud: Cloud) -> None:
    with cloud.db.tx() as c:
        c.execute(
            "INSERT INTO accounts(id, id_hash, channel, hint, created_at, grant_uy, paid_balance_uy) "
            "VALUES ('acct', 'hash', 'email', 'a***@example.com', 0, 100000, 100000)"
        )


def test_provider_key_is_encrypted_and_never_returned() -> None:
    cloud, caller = make_cloud()
    add_account(cloud)
    providers = cloud.add_provider_key(
        caller,
        {"provider": "openrouter", "api_key": "sk-or-secret-1234", "models": ["deepseek/deepseek-v4.1-flash"]},
    )
    assert len(providers) == 1
    shown = providers[0]
    assert shown["provider"] == "openrouter"
    assert shown["key_hint"].endswith("1234")
    assert "secret" not in json.dumps(shown)

    row = cloud.db.account_provider("acct", shown["id"])
    assert row is not None
    assert row["key_enc"] != "sk-or-secret-1234"
    assert cloud.provider_key(caller, shown["id"])["key"] == "sk-or-secret-1234"

    assert cloud.delete_provider_key(caller, "openrouter") is True
    assert cloud.list_provider_keys(caller) == []


def test_byok_matches_provider_and_model() -> None:
    cloud, caller = make_cloud()
    add_account(cloud)
    cloud.add_provider_key(
        caller,
        {"provider": "openrouter", "api_key": "sk-1234", "models": ["deepseek/deepseek-v4.1-flash"]},
    )
    spec = ModelSpec(
        id="deepseek-flash",
        name="DeepSeek Flash",
        upstream="deepseek/deepseek-v4.1-flash",
        provider="openrouter",
    )
    byok = cloud.byok_for(caller, spec)
    assert byok["provider"] == "openrouter"
    assert byok["model"] == "deepseek/deepseek-v4.1-flash"

    other = ModelSpec(id="unknown", name="Unknown", upstream="unknown", provider="polza")
    assert cloud.byok_for(caller, other) is None


def test_two_keys_of_one_provider_and_delete_by_id() -> None:
    cloud, caller = make_cloud()
    add_account(cloud)
    cloud.add_provider_key(caller, {"provider": "openrouter", "label": "Личный", "api_key": "sk-a-1111"})
    cloud.add_provider_key(caller, {"provider": "openrouter", "label": "Рабочий", "api_key": "sk-b-2222"})
    items = cloud.list_provider_keys(caller)
    assert len(items) == 2
    assert {item["label"] for item in items} == {"Личный", "Рабочий"}

    cloud.delete_provider_key(caller, items[0]["id"])
    left = cloud.list_provider_keys(caller)
    assert len(left) == 1
    assert left[0]["id"] == items[1]["id"]


def test_byok_charge_does_not_touch_the_balance() -> None:
    cloud, caller = make_cloud()
    add_account(cloud)
    spec = ModelSpec(
        id="m",
        name="M",
        upstream="m",
        provider="openrouter",
        price_in=1,
        price_out=1,
        sell_in=260,
        sell_out=1040,
    )
    cloud.charge_chat(caller, spec, 1000, 1000, "req-byok", byok=True)

    account = cloud.db.account("acct")
    assert account is not None
    assert account["paid_balance_uy"] == 100000
    with cloud.db.tx() as c:
        row = c.execute("SELECT cost_uy, sale_uy FROM ledger WHERE request_id='req-byok'").fetchone()
    assert row is not None
    assert row["cost_uy"] == 0
    assert row["sale_uy"] == 0


def test_alerts_groups_repeated_errors() -> None:
    cloud, _caller = make_cloud()
    add_account(cloud)
    cloud.note("acct", "upstream.error", "upstream.error 400 deepseek-flash: PI_AI_ERROR")
    cloud.note("acct", "upstream.error", "upstream.error 400 deepseek-flash: PI_AI_ERROR")
    data = cloud.alerts(1)
    assert data["error_groups"][0]["count"] == 2
    assert "PI_AI_ERROR" in data["error_groups"][0]["detail"]


def test_unknown_provider_is_refused() -> None:
    cloud, caller = make_cloud()
    try:
        cloud.add_provider_key(caller, {"provider": "nope", "api_key": "sk-1234"})
    except Exception as error:  # CloudError
        assert getattr(error, "code", "") == "bad_provider"
    else:
        raise AssertionError("unknown provider was accepted")
