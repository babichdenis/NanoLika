"""Scoped device revocation, without real accounts or network calls."""

import json

import pytest

from nanomuse_cloud.config import Settings
from nanomuse_cloud.db import Database
from nanomuse_cloud.hub import Hub
from nanomuse_cloud.senders import LogSender
from nanomuse_cloud.service import Cloud, CloudError


def fixture():
    sender = LogSender()
    db = Database(":memory:")
    cloud = Cloud(Settings(database=":memory:", secret="fixture-secret"), db, sender)
    from nanomuse_cloud.identifiers import parse

    def login(address, device):
        key, _, _ = cloud.sign_in(parse(address), device, "code")
        return {"api_key": key}

    browser = login("owner@example.test", "browser")
    desktop = login("owner@example.test", "computer")
    other = login("other@example.test", "browser")
    caller = cloud.authenticate(browser["api_key"])
    computer = cloud.authenticate(desktop["api_key"])
    stranger = cloud.authenticate(other["api_key"])
    hub = Hub(cloud)
    db.upsert_device(
        caller.account_id,
        "desktop-fixture",
        "Fixture computer",
        "computer",
        "Fixture OS",
        "0.1",
        json.dumps(["chat.open_desktop"]),
        key_hash=computer.key_hash,
    )
    return cloud, db, hub, caller, computer, stranger


@pytest.mark.asyncio
async def test_disconnect_revokes_only_target_session_and_removes_device():
    cloud, db, hub, caller, computer, _ = fixture()
    await hub.disconnect_device(caller, "desktop-fixture")
    assert cloud.key_live(caller.key_hash)
    assert not cloud.key_live(computer.key_hash)
    assert not db.devices_for(caller.account_id)


@pytest.mark.asyncio
async def test_other_account_cannot_disconnect_device():
    cloud, db, hub, caller, computer, stranger = fixture()
    with pytest.raises(CloudError) as error:
        await hub.disconnect_device(stranger, "desktop-fixture")
    assert error.value.status == 404
    assert cloud.key_live(computer.key_hash)
    assert len(db.devices_for(caller.account_id)) == 1


@pytest.mark.asyncio
async def test_cannot_revoke_browser_shared_signin():
    cloud, db, hub, caller, computer, _ = fixture()
    db.upsert_device(caller.account_id, "desktop-fixture", "Fixture computer", "computer", "", "", "[]", key_hash=caller.key_hash)
    with pytest.raises(CloudError) as error:
        await hub.disconnect_device(caller, "desktop-fixture")
    assert error.value.code == "device_shared_session"
    assert cloud.key_live(caller.key_hash)


@pytest.mark.asyncio
async def test_legacy_device_with_unknown_session_is_not_falsely_disconnected():
    cloud, db, hub, caller, computer, _ = fixture()
    db.upsert_device(caller.account_id, "legacy", "Old computer", "computer", "", "", "[]")
    with pytest.raises(CloudError) as error:
        await hub.disconnect_device(caller, "legacy")
    assert error.value.code == "device_session_unknown"
    assert cloud.key_live(computer.key_hash)


def test_session_hash_is_not_exposed_and_empty_updates_keep_binding():
    cloud, db, hub, caller, computer, _ = fixture()
    db.upsert_device(caller.account_id, "desktop-fixture", "Renamed", "computer", "", "", "[]")
    with db.tx() as c:
        assert (
            c.execute("SELECT key_hash FROM devices WHERE account_id=? AND id='desktop-fixture'", (caller.account_id,)).fetchone()[0]
            == computer.key_hash
        )
    assert "key_hash" not in dict(db.devices_for(caller.account_id)[0])
    assert "key_hash" not in hub.devices(caller.account_id)[0]


@pytest.mark.asyncio
async def test_live_connection_is_closed_and_cannot_reconnect():
    from nanomuse_cloud.hub import Connection

    cloud, db, hub, caller, computer, _ = fixture()

    class Socket:
        closed = False

        async def close(self, code, reason):
            self.closed = True

    ws = Socket()
    conn = Connection(
        ws=ws,
        account_id=caller.account_id,
        key_hash=computer.key_hash,
        device_id="desktop-fixture",
        name="Fixture computer",
        kind="computer",
    )
    hub.online[caller.account_id] = {"desktop-fixture": conn}
    await hub.disconnect_device(caller, "desktop-fixture")
    assert ws.closed
    assert "desktop-fixture" not in hub.online.get(caller.account_id, {})
    assert not cloud.key_live(computer.key_hash)


def test_http_disconnect_requires_authentication_and_uses_delete():
    from fastapi.testclient import TestClient

    from nanomuse_cloud.api import create_app
    from nanomuse_cloud.identifiers import parse

    cloud, db, hub, caller, computer, _ = fixture()
    key, _, _ = cloud.sign_in(parse("owner@example.test"), "second browser", "code")
    with TestClient(create_app(cloud.s, cloud)) as client:
        assert client.delete("/v1/link/devices/desktop-fixture").status_code == 401
        response = client.delete("/v1/link/devices/desktop-fixture", headers={"Authorization": "Bearer " + key})
        assert response.status_code == 204, response.text
        assert not cloud.key_live(computer.key_hash)
        assert cloud.authenticate(key).account_id == caller.account_id
