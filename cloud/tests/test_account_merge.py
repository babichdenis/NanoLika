"""Joining a chat's own account to its person's, with the money and the history intact."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from nanomuse_cloud.account_merge import (
    MergeError,
    apply_merge,
    audit,
    backup_database,
    plan_merge,
)
from nanomuse_cloud.channel_links import _telegram_session
from nanomuse_cloud.config import Settings
from nanomuse_cloud.db import Database
from nanomuse_cloud.senders import LogSender
from nanomuse_cloud.service import Cloud, chat_identity

TARGET = "target-account-1"
SOURCE = "source-account-1"


def settings() -> Settings:
    return Settings(database=":memory:", secret="fixture-secret", public_base="https://cloud.example.test")


def seeded(tmp_path: Path, *, surface: str = "telegram", chat: str = "123456789") -> tuple[Database, bytes]:
    """A person's account and the account their chat made, each with history and a key."""
    config = settings()
    key = config.hmac_key
    ident = chat_identity(surface, chat).hash(key)
    database = Database(str(tmp_path / "cloud.db"))
    stamp = int(time.time()) - 3600
    with database.tx() as connection:
        connection.execute(
            "INSERT INTO accounts(id,id_hash,channel,hint,created_at,grant_uy,paid_balance_uy) VALUES(?,?,?,?,?,?,?)",
            (TARGET, "hash-of-an-address", "email", "o***@example.test", stamp, 500, 0),
        )
        connection.execute(
            "INSERT INTO accounts(id,id_hash,channel,hint,created_at,grant_uy,paid_balance_uy) VALUES(?,?,?,?,?,?,?)",
            (SOURCE, ident, surface, "tg ***6789", stamp + 60, 100, 0),
        )
        connection.execute(
            "INSERT INTO sync_conversations(account_id,cid,kind,title,created_at,updated_at,seq) VALUES(?,?,?,?,?,?,?)",
            (TARGET, "target-main", "main", "Main", stamp, stamp, 5),
        )
        connection.execute("INSERT INTO sync_cursors(account_id,seq) VALUES(?,5)", (TARGET,))
        connection.execute(
            "INSERT INTO sync_conversations(account_id,cid,kind,title,created_at,updated_at,seq) VALUES(?,?,?,?,?,?,?)",
            (SOURCE, "chat-main", "main", "The chat", stamp + 60, stamp + 60, 1),
        )
        connection.execute(
            "INSERT INTO sync_messages(account_id,mid,cid,seq,role,text,created_at) VALUES(?,?,?,?,?,?,?)",
            (TARGET, "t-1", "target-main", 1, "user", "from the app", stamp),
        )
        for number, text in enumerate(("hello", "and this is the chat"), start=1):
            connection.execute(
                "INSERT INTO sync_messages(account_id,mid,cid,seq,role,text,created_at) VALUES(?,?,?,?,?,?,?)",
                (SOURCE, f"s-{number}", "chat-main", number, "user", text, stamp + 60 + number),
            )
        connection.execute(
            "INSERT INTO api_keys(key_hash,prefix,account_id,created_at,via) VALUES(?,?,?,?,?)",
            ("chat-key-1", "nmb_chat", SOURCE, stamp + 60, "telegram"),
        )
    return database, key


def test_the_plan_moves_the_chat_and_leaves_money_alone(tmp_path: Path) -> None:
    database, key = seeded(tmp_path)
    plan = plan_merge(
        database,
        target_id=TARGET,
        source_id=SOURCE,
        surface="telegram",
        chat_id="123456789",
        hmac_key=key,
    )
    assert plan.target_main == "target-main"
    assert plan.source_main == "chat-main"
    assert plan.messages == 2
    assert plan.keys == 1
    assert plan.alias is None
    assert any("both sides have a main" in line for line in plan.warnings)
    assert "money  untouched" in plan.describe()
    database.close()


def test_the_merge_moves_history_disables_the_source_and_keeps_its_balance(tmp_path: Path) -> None:
    database, key = seeded(tmp_path)
    plan = plan_merge(
        database,
        target_id=TARGET,
        source_id=SOURCE,
        surface="telegram",
        chat_id="123456789",
        hmac_key=key,
    )
    summary = apply_merge(database, plan)
    assert summary["messages"] == 2
    with database.tx() as connection:
        alias = connection.execute("SELECT account_id FROM account_aliases WHERE id_hash=?", (plan.id_hash,)).fetchone()
        assert alias is not None and alias[0] == TARGET
        source = connection.execute("SELECT * FROM accounts WHERE id=?", (SOURCE,)).fetchone()
        assert source["disabled"] == 1
        assert str(source["id_hash"]).startswith("merged:")
        assert source["grant_uy"] == 100  # never moved, never doubled
        target = connection.execute("SELECT * FROM accounts WHERE id=?", (TARGET,)).fetchone()
        assert target["grant_uy"] == 500
        texts = [row[0] for row in connection.execute("SELECT text FROM sync_messages WHERE account_id=? ORDER BY seq", (TARGET,))]
        assert texts == ["from the app", "hello", "and this is the chat"]
        assert connection.execute("SELECT COUNT(*) FROM sync_messages WHERE account_id=?", (SOURCE,)).fetchone()[0] == 0
        assert (
            connection.execute("SELECT COUNT(*) FROM sync_conversations WHERE account_id=?", (SOURCE,)).fetchone()[0] == 0
        )  # the emptied main row goes with its messages
        keys = connection.execute("SELECT COUNT(*) FROM api_keys WHERE account_id=?", (SOURCE,)).fetchone()[0]
        assert keys == 0
        cursor = connection.execute("SELECT seq FROM sync_cursors WHERE account_id=?", (TARGET,)).fetchone()[0]
        assert cursor == 7  # the two moved messages come after the target's own
    database.close()


def test_a_second_telegram_identity_is_not_merged_twice(tmp_path: Path) -> None:
    database, key = seeded(tmp_path)
    plan = plan_merge(
        database,
        target_id=TARGET,
        source_id=SOURCE,
        surface="telegram",
        chat_id="123456789",
        hmac_key=key,
    )
    apply_merge(database, plan)
    with pytest.raises(MergeError):
        plan_merge(
            database,
            target_id=TARGET,
            source_id=SOURCE,
            surface="telegram",
            chat_id="123456789",
            hmac_key=key,
        )
    database.close()


def test_an_account_with_its_own_address_is_not_a_chat_account(tmp_path: Path) -> None:
    database, key = seeded(tmp_path)
    with database.tx() as connection:
        # a chat account that later took an address of its own is somebody's real account
        connection.execute(
            "INSERT INTO account_aliases(id_hash,account_id,channel,hint,created_at) VALUES(?,?,?,?,?)",
            ("hash-of-an-address", SOURCE, "email", "o***@example.test", int(time.time())),
        )
    with pytest.raises(MergeError):
        plan_merge(
            database,
            target_id=TARGET,
            source_id=SOURCE,
            surface="telegram",
            chat_id="123456789",
            hmac_key=key,
        )
    database.close()


def test_a_chat_another_account_already_owns_is_refused(tmp_path: Path) -> None:
    database, key = seeded(tmp_path)
    with database.tx() as connection:
        connection.execute(
            "INSERT INTO accounts(id,id_hash,channel,hint,created_at) VALUES(?,?,?,?,?)",
            ("somebody-else", "hash-of-somebody", "telegram", "tg ***0000", int(time.time())),
        )
        connection.execute(
            "INSERT INTO account_aliases(id_hash,account_id,channel,hint,created_at,surface) VALUES(?,?,?,?,?,?)",
            (
                chat_identity("telegram", "123456789").hash(key),
                "somebody-else",
                "telegram",
                "tg ***6789",
                int(time.time()),
                "telegram",
            ),
        )
    with pytest.raises(MergeError):
        plan_merge(
            database,
            target_id=TARGET,
            source_id=SOURCE,
            surface="telegram",
            chat_id="123456789",
            hmac_key=key,
        )
    database.close()


def test_a_max_chat_keeps_the_telegram_legacy_hash_apart(tmp_path: Path) -> None:
    """A MAX chat linked before MAX had its own space is filed under the Telegram hash."""
    database, key = seeded(tmp_path, surface="max", chat="777123")
    legacy = chat_identity("telegram", "777123").hash(key)
    with database.tx() as connection:
        connection.execute("UPDATE accounts SET id_hash=? WHERE id=?", (legacy, SOURCE))
        connection.execute(
            "INSERT INTO account_aliases(id_hash,account_id,channel,hint,created_at,surface) VALUES(?,?,?,?,?,?)",
            (legacy, SOURCE, "max", "max ***7123", int(time.time()), "max"),
        )
    plan = plan_merge(
        database,
        target_id=TARGET,
        source_id=SOURCE,
        surface="max",
        chat_id="777123",
        hmac_key=key,
    )
    assert plan.id_hash == legacy
    apply_merge(database, plan)
    with database.tx() as connection:
        row = connection.execute("SELECT account_id, surface FROM account_aliases WHERE id_hash=?", (legacy,)).fetchone()
        assert row["account_id"] == TARGET
        assert row["surface"] == "max"
    database.close()


def test_audit_lists_chat_accounts_per_messenger(tmp_path: Path) -> None:
    database, _key = seeded(tmp_path)
    telegram = audit(database, surface="telegram")
    assert [(row["channel"], row["messages"]) for row in telegram] == [("telegram", 2)]
    assert audit(database, surface="max") == []
    database.close()


def test_the_backup_is_a_usable_copy(tmp_path: Path) -> None:
    database, _key = seeded(tmp_path)
    copy = backup_database(str(tmp_path / "cloud.db"), tmp_path / "backup.db")
    assert copy.is_file()
    spare = Database(str(copy))
    try:
        with spare.tx() as connection:
            found = connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        assert found == 2
    finally:
        spare.close()
    database.close()


def test_after_the_merge_the_mini_app_finds_the_person_s_account(tmp_path: Path) -> None:
    """The point of the merge: the same chat now resolves to the account, not to its own."""
    database, key = seeded(tmp_path)
    plan = plan_merge(
        database,
        target_id=TARGET,
        source_id=SOURCE,
        surface="telegram",
        chat_id="123456789",
        hmac_key=key,
    )
    apply_merge(database, plan)
    database.close()

    config = Settings(
        database=str(tmp_path / "cloud.db"),
        secret="fixture-secret",
        public_base="https://cloud.example.test",
    )
    cloud = Cloud(config, Database(str(tmp_path / "cloud.db")), LogSender())
    answer = _telegram_session(cloud, 123456789)
    assert answer["account"]["id"] == TARGET
