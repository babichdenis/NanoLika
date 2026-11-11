"""Safety constraints a future delivery-retention migration must preserve.

The first test demonstrates why deleting the last per-message receipt without
an independently enforced replay watermark is unsafe; it is not a cleanup policy.
"""

from __future__ import annotations

import hashlib
import uuid
from types import SimpleNamespace

import pytest

from nanomuse_cloud.db import Database
from nanomuse_cloud.delivery_journal import DeliveryJournal
from nanomuse_cloud.sync import SyncStore


def request() -> dict:
    return {
        "mid": str(uuid.uuid4()), "channel": "telegram", "chat": "123|9",
        "chunks": [hashlib.sha256(b"message").hexdigest()],
    }


def account(db: Database) -> str:
    return str(db.create_account(str(uuid.uuid4()), "email", "hint", 0)["id"])


def claim(body: dict, token: str = "A" * 32) -> dict:
    return {**body, "part": 0, "claim": token}


def test_removing_last_confirmed_receipt_makes_old_message_indistinguishable_from_new(tmp_path):
    db = Database(str(tmp_path / "cloud.sqlite3"))
    owner = account(db)
    journal, body = DeliveryJournal(db), request()
    journal.prepare(owner, body)
    assert journal.claim(owner, claim(body))["state"] == "claimed"
    journal.finish(owner, {**claim(body), "state": "sent", "remote_id": "platform-1"})
    assert journal.claim(owner, claim(body, "B" * 32))["state"] == "sent"
    # Simulate a hypothetical retention job deleting a supposedly old receipt.
    # An offline device can replay the very same opaque UUID and target later.
    with db.tx_durable() as c:
        c.execute("DELETE FROM delivery_journal WHERE account_id=? AND mid=?", (owner, body["mid"]))
    assert journal.prepare(owner, body)["parts"][0]["state"] == "pending"
    assert journal.claim(owner, claim(body, "B" * 32))["state"] == "claimed"
    db.close()


@pytest.mark.parametrize("outcome", ["sending", "sent"])
def test_disabling_sync_preserves_delivery_safety_record(tmp_path, outcome):
    db = Database(str(tmp_path / "cloud.sqlite3"))
    owner = account(db)
    store, journal, body = SyncStore(db), DeliveryJournal(db), request()
    store.set_enabled(owner, True)
    journal.prepare(owner, body)
    assert journal.claim(owner, claim(body))["state"] == "claimed"
    if outcome == "sent":
        journal.finish(owner, {**claim(body), "state": "sent", "remote_id": "platform-1"})
    store.set_enabled(owner, False)
    # Deleting sync data does not erase the separate delivery-safety record.
    assert journal.prepare(owner, body)["parts"][0]["state"] == outcome
    assert journal.claim(owner, claim(body, "B" * 32))["state"] == outcome
    db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["unknown_mid", "sync_off", "network_failure"])
async def test_old_desktop_does_not_fail_closed_when_relay_cannot_arbitrate(code):
    from nanomuse.channels.manager import ChannelManager
    from nanomuse.cloud import CloudError

    async def fail_claim(*_args):
        raise CloudError(404 if code == "unknown_mid" else 503, code, "unavailable")

    old_client = SimpleNamespace(claim_delivery=fail_claim)
    manager = object.__new__(ChannelManager)
    manager.svc = SimpleNamespace(sync=SimpleNamespace(active=True, client=old_client), hub=SimpleNamespace(device_id="old"))
    # The existing installed desktop would send despite the failed arbitration.
    assert await manager._claim_synced_delivery(str(uuid.uuid4()), "telegram", "123|9") is True
