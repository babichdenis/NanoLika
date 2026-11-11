from __future__ import annotations

import asyncio
import os
import sqlite3
import stat

import pytest

from nanomuse.channels.cloud_delivery import CloudDelivery, DeliveryConflict, DeliveryDeferred


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["telegram", "max"])
async def test_ordered_delivery_is_idempotent_and_accounts_do_not_mix(tmp_path, channel):
    path = tmp_path / "channels" / "delivery.sqlite3"
    delivery = CloudDelivery(path)
    calls = []

    async def send(part, text):
        calls.append((part, text))
        return f"remote-{part}"

    for account in ("account-a", "account-b"):
        result = await delivery.deliver(account, channel, "sender", "same-chat", "same-operation", ["one", "two"], send)
        assert result == {"state": "sent", "sent": 2}
    assert calls == [(0, "one"), (1, "two"), (0, "one"), (1, "two")]
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT remote_id FROM cloud_delivery_parts WHERE account='account-a' ORDER BY part"
        ).fetchall() == [("remote-0",), ("remote-1",)]
    again = CloudDelivery(path)
    assert await again.deliver("account-a", channel, "sender", "same-chat", "same-operation", ["one", "two"], send) == {
        "state": "sent", "sent": 2
    }
    assert len(calls) == 4
    with pytest.raises(DeliveryConflict):
        await again.deliver("account-a", channel, "sender", "other-chat", "same-operation", ["one", "two"], send)
    with pytest.raises(DeliveryConflict):
        await again.deliver("account-a", channel, "sender", "same-chat", "same-operation", ["different"], send)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["telegram", "max"])
async def test_timeout_never_repeats_confirmed_or_ambiguous_part(tmp_path, channel):
    path = tmp_path / "delivery.sqlite3"
    delivery = CloudDelivery(path)
    attempted = []

    async def send(part, text):
        attempted.append((part, text))
        if part == 1:
            raise TimeoutError("platform may have accepted the request")
        return f"remote-{part}"

    args = ("account", channel, "sender", "chat-topic", "operation", ["a", "b", "c"])
    assert await delivery.deliver(*args, send) == {"state": "unknown", "sent": 1}
    assert await CloudDelivery(path).deliver(*args, send) == {"state": "unknown", "sent": 1}
    assert attempted == [(0, "a"), (1, "b")]


@pytest.mark.asyncio
async def test_crash_after_sending_state_is_never_automatically_retried(tmp_path):
    path = tmp_path / "delivery.sqlite3"
    delivery = CloudDelivery(path)
    chunks = ("a", "b")
    delivery._reserve("a", "telegram", "sender", "topic", "op", chunks)
    assert delivery._next("a", "telegram", "op", "dead-process") == ("send", 0, 0)
    sent = []

    async def send(part, text):
        sent.append((part, text))

    assert await CloudDelivery(path).deliver("a", "telegram", "sender", "topic", "op", chunks, send) == {
        "state": "in_progress", "sent": 0
    }
    assert sent == []
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT state FROM cloud_delivery_parts ORDER BY part").fetchall() == [("sending",), ("pending",)]


@pytest.mark.asyncio
async def test_parallel_duplicate_cannot_send_twice_or_overtake(tmp_path):
    path = tmp_path / "delivery.sqlite3"
    first, second = CloudDelivery(path), CloudDelivery(path)
    started, finish = asyncio.Event(), asyncio.Event()
    calls = []

    async def send(part, text):
        calls.append((part, text))
        if part == 0:
            started.set()
            await finish.wait()
        return f"remote-{part}"

    args = ("account", "telegram", "sender", "topic", "op", ["a", "b"])
    task = asyncio.create_task(first.deliver(*args, send))
    await started.wait()
    assert await second.deliver(*args, send) == {"state": "in_progress", "sent": 0}
    assert calls == [(0, "a")]
    finish.set()
    assert await task == {"state": "sent", "sent": 2}
    assert calls == [(0, "a"), (1, "b")]


@pytest.mark.asyncio
async def test_cancelled_send_remains_unknown_after_restart(tmp_path):
    path = tmp_path / "delivery.sqlite3"
    started = asyncio.Event()

    async def send(part, text):
        started.set()
        await asyncio.Event().wait()

    args = ("account", "max", "sender", "chat", "op", ["a"])
    task = asyncio.create_task(CloudDelivery(path).deliver(*args, send))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await CloudDelivery(path).deliver(*args, send) == {"state": "unknown", "sent": 0}


@pytest.mark.asyncio
async def test_restart_after_confirmed_part_resumes_only_pending_parts(tmp_path):
    path = tmp_path / "delivery.sqlite3"
    before = CloudDelivery(path)
    chunks = ("first", "second", "third")
    before._reserve("a", "telegram", "sender", "topic", "op", chunks)
    assert before._next("a", "telegram", "op", "claim") == ("send", 0, 0)
    before._finish("a", "telegram", "op", 0, "claim", "sent", "remote-first")
    calls = []

    async def send(part, text):
        calls.append((part, text))
        return f"remote-{part}"

    assert await CloudDelivery(path).deliver("a", "telegram", "sender", "topic", "op", chunks, send) == {
        "state": "sent", "sent": 3
    }
    assert calls == [(1, "second"), (2, "third")]


@pytest.mark.asyncio
async def test_missing_platform_confirmation_is_unknown(tmp_path):
    path = tmp_path / "delivery.sqlite3"

    async def send(part, text):
        return ""

    args = ("account", "max", "sender", "chat", "op", ["a"])
    assert await CloudDelivery(path).deliver(*args, send) == {"state": "unknown", "sent": 0}
    assert await CloudDelivery(path).deliver(*args, send) == {"state": "unknown", "sent": 0}


@pytest.mark.asyncio
async def test_relay_deferral_before_platform_request_can_be_retried(tmp_path):
    path = tmp_path / "delivery.sqlite3"
    args = ("account", "max", "sender", "chat", "op", ["a"])
    calls = []

    async def deferred(part, text):
        calls.append("relay denied claim; platform untouched")
        raise DeliveryDeferred()

    async def confirmed(part, text):
        calls.append("platform request")
        return "platform-id"

    assert await CloudDelivery(path).deliver(*args, deferred) == {"state": "in_progress", "sent": 0}
    assert await CloudDelivery(path).deliver(*args, confirmed) == {"state": "sent", "sent": 1}
    assert calls == ["relay denied claim; platform untouched", "platform request"]


@pytest.mark.asyncio
async def test_explicit_retry_is_scoped_and_idempotent(tmp_path):
    journal = CloudDelivery(tmp_path / "delivery.sqlite3")
    chunks = ("a", "b")
    journal._reserve("a", "telegram", "sender", "topic", "op", chunks)
    assert journal._next("a", "telegram", "op", "old-claim") == ("send", 0, 0)
    with pytest.raises(DeliveryConflict):
        journal.retry_unknown("other-account", "telegram", "op", 0, "D" * 32)
    journal.retry_unknown("a", "telegram", "op", 0, "D" * 32)
    assert journal._next("a", "telegram", "op", "new-claim") == ("send", 0, 0)
    with pytest.raises(RuntimeError):
        journal._finish("a", "telegram", "op", 0, "old-claim", "sent", "old-id")
    # Duplicate confirmation must not reset an already running new attempt.
    journal.retry_unknown("a", "telegram", "op", 0, "D" * 32)
    assert journal._next("a", "telegram", "op", "third-claim") == ("in_progress", 0, 0)
    journal._finish("a", "telegram", "op", 0, "new-claim", "sent", "new-id")
    journal.retry_unknown("a", "telegram", "op", 0, "D" * 32)
    with pytest.raises(DeliveryConflict):
        journal.retry_unknown("a", "telegram", "op", 1, "D" * 32)
    with pytest.raises(DeliveryConflict):
        journal.retry_unknown("a", "telegram", "op", 0, "E" * 32)


@pytest.mark.asyncio
async def test_reconcile_trusted_sent_without_platform_retry(tmp_path):
    journal = CloudDelivery(tmp_path / "delivery.sqlite3")
    chunks = ("a", "b")
    journal._reserve("a", "telegram", "sender", "topic", "op", chunks)
    assert journal._next("a", "telegram", "op", "old") == ("send", 0, 0)
    journal._finish("a", "telegram", "op", 0, "old", "unknown")
    with pytest.raises(DeliveryConflict):
        journal.confirm_sent("a", "telegram", "other-sender", "topic", "op", chunks, 0, "id-0")
    with pytest.raises(DeliveryConflict):
        journal.confirm_sent("a", "telegram", "sender", "other-topic", "op", chunks, 0, "id-0")
    with pytest.raises(DeliveryConflict):
        journal.confirm_sent("a", "telegram", "sender", "topic", "op", chunks, 1, "id-1")
    journal.confirm_sent("a", "telegram", "sender", "topic", "op", chunks, 0, "id-0")
    journal.confirm_sent("a", "telegram", "sender", "topic", "op", chunks, 0, "id-0")
    with pytest.raises(DeliveryConflict):
        journal.confirm_sent("a", "telegram", "sender", "topic", "op", chunks, 0, "different")
    calls = []

    async def send(part, text):
        calls.append((part, text))
        return "id-1"

    assert await journal.deliver("a", "telegram", "sender", "topic", "op", chunks, send) == {
        "state": "sent", "sent": 2
    }
    assert calls == [(1, "b")]


@pytest.mark.asyncio
async def test_confirmed_relay_retry_can_leave_local_pending_part_pending(tmp_path):
    journal = CloudDelivery(tmp_path / "delivery.sqlite3")
    journal._reserve("a", "telegram", "sender", "topic", "op", ("a",))
    journal.retry_unknown("a", "telegram", "op", 0, "D" * 32)
    assert journal._next("a", "telegram", "op", "next") == ("send", 0, 0)


def test_local_journal_quotas_preserve_existing_deliveries(tmp_path, monkeypatch):
    from nanomuse.channels import cloud_delivery
    from nanomuse.channels.cloud_delivery import DeliveryFull

    monkeypatch.setattr(cloud_delivery, "MAX_ACCOUNT_DELIVERIES", 1)
    monkeypatch.setattr(cloud_delivery, "MAX_ACCOUNT_PARTS", 2)
    journal = CloudDelivery(tmp_path / "delivery.sqlite3")
    journal.prepare("a", "telegram", "sender", "topic", "op", ("a", "b"))
    journal.prepare("a", "telegram", "sender", "topic", "op", ("a", "b"))
    with pytest.raises(DeliveryFull):
        journal.prepare("a", "telegram", "sender", "topic", "other", ("c",))
    journal.prepare("b", "telegram", "sender", "topic", "other", ("c",))
    assert journal._next("a", "telegram", "op", "claim") == ("send", 0, 0)
