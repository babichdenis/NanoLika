import hashlib

import pytest
from test_accounts import auth, make
from test_sync import conv, msg, signed_in, uid


@pytest.mark.asyncio
async def test_messenger_routes_are_explicitly_opt_in(monkeypatch):
    monkeypatch.delenv("NANOMUSE_ACCOUNT_MESSENGER_BRIDGE", raising=False)
    app, client, *_ = make()
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/v1/delivery/prepare" not in paths
    assert "/v1/bot/runtime/message" not in paths
    await client.aclose()

    monkeypatch.setenv("NANOMUSE_ACCOUNT_MESSENGER_BRIDGE", "1")
    app, client, *_ = make()
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/v1/delivery/prepare" in paths
    assert "/v1/bot/runtime/message" in paths
    await client.aclose()


@pytest.mark.asyncio
async def test_legacy_delivery_works_without_optional_journal(monkeypatch):
    monkeypatch.delenv("NANOMUSE_ACCOUNT_MESSENGER_BRIDGE", raising=False)
    app, client, sender, *_ = make()
    key = await signed_in(client, sender)
    cid, mid = uid(), uid()
    headers = auth(key)
    changes = await client.post(
        "/v1/sync/changes", json={"device": "pc", "conversations": [conv(cid)], "messages": [msg(mid, cid)]},
        headers=headers,
    )
    assert changes.status_code == 200
    with app.state.cloud.db.tx() as c:
        c.execute("DROP TABLE delivery_journal")
    claim = await client.post(
        "/v1/sync/delivery/claim",
        json={"mid": mid, "channel": "telegram", "chat": "topic", "device": "pc"}, headers=headers,
    )
    assert claim.status_code == 200, claim.text
    await client.aclose()


@pytest.mark.asyncio
async def test_disabled_bridge_still_honors_existing_delivery_journal(monkeypatch):
    from nanomuse_cloud.delivery_journal import DeliveryJournal

    monkeypatch.delenv("NANOMUSE_ACCOUNT_MESSENGER_BRIDGE", raising=False)
    app, client, sender, _, cloud, _ = make()
    key = await signed_in(client, sender)
    headers = auth(key)
    cid, mid = uid(), uid()
    changes = await client.post(
        "/v1/sync/changes", json={"device": "pc", "conversations": [conv(cid)], "messages": [msg(mid, cid)]},
        headers=headers,
    )
    assert changes.status_code == 200
    account = cloud.authenticate(key).account_id
    DeliveryJournal(cloud.db).prepare(account, {
        "mid": mid, "channel": "telegram", "chat": "topic", "chunks": [hashlib.sha256(b"answer").hexdigest()],
    })
    claim = await client.post(
        "/v1/sync/delivery/claim",
        json={"mid": mid, "channel": "telegram", "chat": "topic", "device": "pc"}, headers=headers,
    )
    assert claim.status_code == 409 and claim.json()["error"]["code"] == "delivery_claimed"
    await client.aclose()
