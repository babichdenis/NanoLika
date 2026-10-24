"""Future payment notices have transactional identity and retry-safe delivery."""

import asyncio
import json

import pytest
from test_accounts import auth
from test_payments import setup

from nanomuse_cloud.payment_outbox import PaymentOutbox


async def fixture(monkeypatch):
    _, client, user, p, _, _ = await setup(monkeypatch)
    order = (
        await client.post(
            "/v1/payments/orders",
            headers=auth(user["api_key"]),
            json={"amount": "200", "accept_terms": True, "idempotency_key": "outbox_order_fixture"},
        )
    ).json()
    outbox = PaymentOutbox(p.db)
    return client, p, outbox, order["order_id"]


def settle(c, order_id):
    # Simulated future settlement. No production path is enabled by these tests.
    c.execute("UPDATE tbank_orders SET terminal='fixture-live',status='CONFIRMED',credited=1 WHERE id=?", (order_id,))


async def test_demo_and_unsettled_orders_never_enqueue(monkeypatch):
    client, p, outbox, oid = await fixture(monkeypatch)
    with p.db.tx() as c:
        with pytest.raises(ValueError, match="demo_forbidden"):
            outbox.enqueue_settled(c, oid, kind="payment_confirmed")
        c.execute("UPDATE tbank_orders SET terminal='fixture-live' WHERE id=?", (oid,))
        with pytest.raises(ValueError, match="unsettled"):
            outbox.enqueue_settled(c, oid, kind="payment_confirmed")
        assert c.execute("SELECT COUNT(*) FROM payment_notice_outbox").fetchone()[0] == 0
    await client.aclose()


async def test_rollback_removes_notice_and_settlement_together(monkeypatch):
    client, p, outbox, oid = await fixture(monkeypatch)
    with pytest.raises(RuntimeError):
        with p.db.tx() as c:
            settle(c, oid)
            outbox.enqueue_settled(c, oid, kind="payment_confirmed", now=100)
            raise RuntimeError("rollback")
    assert not p.order(oid)["credited"]
    assert outbox.claim(now=1000) is None
    await client.aclose()


async def test_duplicate_capture_and_distinct_cumulative_refunds(monkeypatch):
    client, p, outbox, oid = await fixture(monkeypatch)
    with p.db.tx() as c:
        settle(c, oid)
        first = outbox.enqueue_settled(c, oid, kind="payment_confirmed", now=100)
        assert outbox.enqueue_settled(c, oid, kind="payment_confirmed", now=101) == first
        c.execute("UPDATE tbank_orders SET status='PARTIAL_REFUNDED',refunded=5000 WHERE id=?", (oid,))
        partial = outbox.enqueue_settled(c, oid, kind="payment_refunded", now=102)
        assert outbox.enqueue_settled(c, oid, kind="payment_refunded", now=103) == partial
        c.execute("UPDATE tbank_orders SET status='REFUNDED',refunded=20000 WHERE id=?", (oid,))
        full = outbox.enqueue_settled(c, oid, kind="payment_refunded", now=104)
        assert len({first, partial, full}) == 3
        assert c.execute("SELECT COUNT(*) FROM payment_notice_outbox").fetchone()[0] == 3
    await client.aclose()


async def test_expired_lease_recovery_rejects_stale_ack_and_keeps_identity(monkeypatch):
    client, p, outbox, oid = await fixture(monkeypatch)
    with p.db.tx() as c:
        settle(c, oid)
        eid = outbox.enqueue_settled(c, oid, kind="payment_confirmed", now=100)
    original = outbox.claim(now=100, lease_seconds=10)
    assert outbox.claim(now=109) is None
    recovered = outbox.claim(now=110)
    assert recovered["event_id"] == eid and recovered["attempts"] == 2
    assert recovered["lease_token"] != original["lease_token"]
    assert not outbox.acknowledge(original, now=111)
    assert outbox.acknowledge(recovered, now=111)
    assert outbox.claim(now=1000) is None
    await client.aclose()


async def test_delivery_is_outside_transaction_and_retry_does_not_leak_errors(monkeypatch):
    client, p, outbox, oid = await fixture(monkeypatch)
    with p.db.tx() as c:
        settle(c, oid)
        eid = outbox.enqueue_settled(c, oid, kind="payment_confirmed", now=100)
    attempts = []

    async def sink(event, idempotency):
        # Would deadlock if dispatcher held Database.tx's non-reentrant lock.
        await asyncio.wait_for(asyncio.to_thread(p.db.account_counts), timeout=2)
        assert idempotency == eid == event["event_id"]
        assert "email" not in event
        attempts.append(idempotency)
        if len(attempts) == 1:
            raise RuntimeError("secret address person@example.test and token")

    assert not await outbox.dispatch_one(sink, now=100)
    with p.db.tx() as c:
        row = dict(c.execute("SELECT * FROM payment_notice_outbox").fetchone())
    assert row["next_attempt_at"] == 130 and row["last_error"] == "delivery_failed"
    assert "person@example.test" not in json.dumps(row)
    assert not await outbox.dispatch_one(sink, now=129)
    assert await outbox.dispatch_one(sink, now=130)
    assert attempts == [eid, eid]
    assert not await outbox.dispatch_one(sink, now=1000)
    await client.aclose()


async def test_parallel_dispatchers_cannot_claim_same_unexpired_job(monkeypatch):
    client, p, outbox, oid = await fixture(monkeypatch)
    with p.db.tx() as c:
        settle(c, oid)
        outbox.enqueue_settled(c, oid, kind="payment_confirmed", now=100)
    delivered = []

    async def sink(event, key):
        await asyncio.sleep(0)
        delivered.append(key)

    results = await asyncio.gather(outbox.dispatch_one(sink, now=100), outbox.dispatch_one(sink, now=100))
    assert sorted(results) == [False, True] and len(delivered) == 1
    await client.aclose()


async def test_hook_refuses_autocommit_connection(monkeypatch):
    client, p, outbox, oid = await fixture(monkeypatch)
    with pytest.raises(ValueError, match="transaction_required"):
        outbox.enqueue_settled(p.db._conn, oid, kind="payment_confirmed")
    await client.aclose()
