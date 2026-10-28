"""Legacy forget is an offline catalogue operation, not session revocation."""

from __future__ import annotations

import pytest
from test_device_disconnect import fixture

from nanomuse_cloud.hub import Connection
from nanomuse_cloud.service import CloudError


class Socket:
    def __init__(self):
        self.closed = False

    async def close(self, code, reason):
        self.closed = True


def test_legacy_forget_refuses_a_live_device_without_claiming_disconnection():
    cloud, db, hub, caller, computer, _ = fixture()
    socket = Socket()
    hub.online[caller.account_id] = {
        "desktop-fixture": Connection(
            ws=socket,
            account_id=caller.account_id,
            key_hash=computer.key_hash,
            device_id="desktop-fixture",
            name="Fixture computer",
            kind="computer",
        )
    }
    with pytest.raises(CloudError) as error:
        hub.forget(caller.account_id, "desktop-fixture")
    assert error.value.status == 409 and error.value.code == "device_online"
    assert not socket.closed
    assert "desktop-fixture" in hub.online[caller.account_id]
    assert len(db.devices_for(caller.account_id)) == 1
    assert cloud.key_live(computer.key_hash)


def test_forgetting_offline_entry_does_not_revoke_the_devices_signin():
    cloud, db, hub, caller, computer, _ = fixture()
    hub.forget(caller.account_id, "desktop-fixture")
    assert not db.devices_for(caller.account_id)
    assert cloud.key_live(computer.key_hash)
    assert cloud.key_live(caller.key_hash)


def test_legacy_forget_cannot_remove_another_accounts_device():
    cloud, db, hub, caller, computer, stranger = fixture()
    hub.forget(stranger.account_id, "desktop-fixture")
    assert len(db.devices_for(caller.account_id)) == 1
    assert cloud.key_live(computer.key_hash)
