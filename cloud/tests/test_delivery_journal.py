from __future__ import annotations

import asyncio
import hashlib
import uuid

import pytest
from test_accounts import auth, make
from test_cloud import sign_up
from test_sync import signed_in


@pytest.fixture(autouse=True)
def enable_experimental_delivery_routes(monkeypatch):
    monkeypatch.setenv("NANOMUSE_ACCOUNT_MESSENGER_BRIDGE", "1")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["telegram", "max"])
async def test_shared_journal_orders_and_never_reclaims_ambiguous_parts(channel):
    _app, client, sender, _up, _cloud, _settings = make()
    account_a = await signed_in(client, sender)
    account_b = await signed_in(client, sender, identifier="13800138001")
    second_device = (await sign_up(client, sender, identifier="13800138000", device="second-device"))["api_key"]
    assert second_device != account_a
    body = {"mid": str(uuid.uuid4()), "channel": channel, "chat": "same-chat-topic", "chunks": [digest("a"), digest("b")]}
    a, b = auth(account_a), auth(account_b)
    prepare = await client.post("/v1/delivery/prepare", json=body, headers=a)
    assert prepare.status_code == 200
    assert [part["state"] for part in prepare.json()["parts"]] == ["pending", "pending"]
    mismatch = await client.post("/v1/delivery/prepare", json={**body, "chunks": [digest("other")]}, headers=a)
    assert mismatch.status_code == 409 and mismatch.json()["error"]["code"] == "delivery_conflict"
    # Two machines race for one part; exactly one receives the right to send.
    req = {**body, "part": 0}
    first, second = await asyncio.gather(
        client.post("/v1/delivery/claim", json={**req, "claim": "A" * 32}, headers=a),
        client.post("/v1/delivery/claim", json={**req, "claim": "B" * 32}, headers=auth(second_device)),
    )
    assert {first.json()["state"], second.json()["state"]} == {"claimed", "sending"}
    owner = "A" * 32 if first.json()["state"] == "claimed" else "B" * 32
    other = "B" * 32 if owner == "A" * 32 else "A" * 32
    later = await client.post("/v1/delivery/claim", json={**body, "part": 1, "claim": "C" * 32}, headers=a)
    assert later.json()["state"] == "blocked"
    wrong_finish = await client.post(
        "/v1/delivery/finish", json={**req, "claim": other, "state": "sent", "remote_id": "platform-1"}, headers=a
    )
    assert wrong_finish.status_code == 409
    # No timeout can steal 'sending', even after a competing process restarts.
    again = await client.post("/v1/delivery/claim", json={**req, "claim": "D" * 32}, headers=a)
    assert again.json()["state"] == "sending"
    done_body = {**req, "claim": owner, "state": "sent", "remote_id": "platform-1"}
    done = await client.post("/v1/delivery/finish", json=done_body, headers=a)
    assert done.json()["state"] == "sent"
    # Losing a confirmation response is safe: the same completion can be replayed.
    assert (await client.post("/v1/delivery/finish", json=done_body, headers=a)).json()["state"] == "sent"
    assert (await client.post("/v1/delivery/claim", json={**req, "claim": "E" * 32}, headers=a)).json()["state"] == "sent"
    claim2 = {**body, "part": 1, "claim": "F" * 32}
    assert (await client.post("/v1/delivery/claim", json=claim2, headers=a)).json()["state"] == "claimed"
    assert (await client.post("/v1/delivery/finish", json={**claim2, "state": "unknown"}, headers=a)).json()["state"] == "unknown"
    assert (await client.post("/v1/delivery/claim", json={**claim2, "claim": "G" * 32}, headers=a)).json()["state"] == "unknown"
    # Same message and target in a different account remain completely independent.
    assert (await client.post("/v1/delivery/prepare", json=body, headers=b)).status_code == 200
    assert (await client.post("/v1/delivery/claim", json={**req, "claim": "H" * 32}, headers=b)).json()["state"] == "claimed"
    assert (await client.post("/v1/delivery/prepare", json=body)).status_code in {401, 403}


@pytest.mark.asyncio
async def test_legacy_claim_and_durable_journal_exclude_each_other_atomically():
    from test_sync import conv, msg, uid

    app, client, sender, _up, cloud, _settings = make()
    key = await signed_in(client, sender)
    headers = auth(key)
    account = cloud.authenticate(key).account_id
    cid, first, second = uid(), uid(), uid()
    await client.post(
        "/v1/sync/changes",
        json={"device": "pc", "conversations": [conv(cid)], "messages": [msg(first, cid), msg(second, cid)]},
        headers=headers,
    )
    legacy = {"mid": first, "channel": "telegram", "chat": "chat-topic", "device": "pc"}
    assert (await client.post("/v1/sync/delivery/claim", json=legacy, headers=headers)).status_code == 200
    # Even an old claim may mean the provider accepted a message; never take it over.
    with app.state.cloud.db.tx() as c:
        c.execute("UPDATE sync_deliveries SET claimed_at=0 WHERE account_id=? AND mid=?", (account, first))
    body = {"mid": first, "channel": "telegram", "chat": "chat-topic", "chunks": [digest("a")]}
    blocked = await client.post("/v1/delivery/prepare", json=body, headers=headers)
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "legacy_delivery_unknown"

    body["mid"] = second
    assert (await client.post("/v1/delivery/prepare", json=body, headers=headers)).status_code == 200
    legacy["mid"] = second
    blocked = await client.post("/v1/sync/delivery/claim", json=legacy, headers=headers)
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "delivery_claimed"


def test_journal_survives_relay_restart_without_replaying_sending(tmp_path):
    from nanomuse_cloud.db import Database
    from nanomuse_cloud.delivery_journal import DeliveryJournal

    path = str(tmp_path / "cloud.sqlite3")
    db = Database(path)
    account = str(db.create_account("id-hash", "email", "hint", 0)["id"])
    journal = DeliveryJournal(db)
    payload = {"mid": str(uuid.uuid4()), "channel": "telegram", "chat": "1|2", "chunks": [digest("part")]}
    claim = {**payload, "part": 0, "claim": "X" * 32}
    journal.prepare(account, payload)
    assert journal.claim(account, claim)["state"] == "claimed"
    db.close()
    reopened = Database(path)
    journal = DeliveryJournal(reopened)
    assert journal.prepare(account, payload)["parts"][0]["state"] == "sending"
    assert journal.claim(account, {**claim, "claim": "Y" * 32})["state"] == "sending"
    reopened.close()


@pytest.mark.asyncio
async def test_lost_confirmation_does_not_resend_and_blocks_old_device():
    _app, client, sender, _up, _cloud, _settings = make()
    device_one = await signed_in(client, sender)
    device_two = (await sign_up(client, sender, identifier="13800138000", device="other"))["api_key"]
    base = {"mid": str(uuid.uuid4()), "channel": "telegram", "chat": "-123|7", "chunks": [digest("a"), digest("b")]}
    first, second = auth(device_one), auth(device_two)
    assert (await client.post("/v1/delivery/prepare", json=base, headers=first)).status_code == 200
    claim = {**base, "part": 0, "claim": "Q" * 32}
    assert (await client.post("/v1/delivery/claim", json=claim, headers=first)).json()["state"] == "claimed"
    platform_calls = ["part-0 accepted"]
    # Relay commits the confirmation; pretend the HTTP response is lost in transit.
    await client.post("/v1/delivery/finish", json={**claim, "state": "sent", "remote_id": "11"}, headers=first)
    retried = await client.post("/v1/delivery/claim", json={**claim, "claim": "R" * 32}, headers=second)
    if retried.json()["state"] == "claimed":
        platform_calls.append("part-0 resent")
    assert retried.json()["state"] == "sent"
    legacy = await client.post(
        "/v1/sync/delivery/claim",
        json={"mid": base["mid"], "channel": base["channel"], "chat": base["chat"], "device": "desktop"},
        headers=second,
    )
    assert legacy.status_code == 409 and legacy.json()["error"]["code"] == "delivery_claimed"
    # The platform may accept part 1 before the process crashes without a finish call.
    pending_finish = {**base, "part": 1, "claim": "S" * 32}
    assert (await client.post("/v1/delivery/claim", json=pending_finish, headers=first)).json()["state"] == "claimed"
    platform_calls.append("part-1 may be accepted")
    after_crash = await client.post("/v1/delivery/claim", json={**pending_finish, "claim": "T" * 32}, headers=second)
    if after_crash.json()["state"] == "claimed":
        platform_calls.append("part-1 resent")
    assert after_crash.json()["state"] == "sending"
    assert platform_calls == ["part-0 accepted", "part-1 may be accepted"]


@pytest.mark.asyncio
async def test_malformed_destination_and_state_are_rejected():
    _app, client, sender, _up, _cloud, _settings = make()
    key = await signed_in(client, sender)
    headers = auth(key)
    base = {"mid": str(uuid.uuid4()), "channel": "telegram", "chat": "1", "chunks": [digest("x")]}
    assert (await client.post("/v1/delivery/prepare", json={**base, "channel": []}, headers=headers)).status_code == 400
    assert (await client.post("/v1/delivery/prepare", json=base, headers=headers)).status_code == 200
    assert (await client.post(
        "/v1/delivery/finish", json={**base, "part": 0, "claim": "A" * 32, "state": []}, headers=headers
    )).status_code == 400


def test_durable_transaction_restores_synchronous_and_rolls_back_even_on_base_exception(tmp_path):
    from nanomuse_cloud.db import Database

    class Abort(BaseException):
        pass

    db = Database(str(tmp_path / "cloud.sqlite3"))
    with db.tx() as c:
        before = c.execute("PRAGMA synchronous").fetchone()[0]
    with pytest.raises(Abort):
        with db.tx_durable() as c:
            assert c.execute("PRAGMA synchronous").fetchone()[0] == 2
            c.execute("INSERT INTO events(account_id,ts,kind) VALUES ('', 1, 'rollback-test')")
            raise Abort()
    with db.tx() as c:
        assert c.execute("PRAGMA synchronous").fetchone()[0] == before
        assert not c.execute("SELECT 1 FROM events WHERE kind='rollback-test'").fetchone()
        with pytest.raises(RuntimeError, match="cannot be nested"):
            with db.tx_durable():
                pass
    with db.tx_durable() as c:
        c.execute("INSERT INTO events(account_id,ts,kind) VALUES ('', 1, 'committed-test')")
    with db.tx() as c:
        assert c.execute("PRAGMA synchronous").fetchone()[0] == before
        assert c.execute("SELECT 1 FROM events WHERE kind='committed-test'").fetchone()
    db.close()


def test_durable_claim_serializes_two_independent_relay_connections(tmp_path):
    import concurrent.futures
    import threading

    from nanomuse_cloud.db import Database
    from nanomuse_cloud.delivery_journal import DeliveryJournal

    path = str(tmp_path / "cloud.sqlite3")
    first, second = Database(path), Database(path)
    account = str(first.create_account("hash", "email", "hint", 0)["id"])
    j1, j2 = DeliveryJournal(first), DeliveryJournal(second)
    base = {"mid": str(uuid.uuid4()), "channel": "telegram", "chat": "1|2", "chunks": [digest("a")]}
    j1.prepare(account, base)
    barrier = threading.Barrier(2)

    def attempt(journal, token):
        barrier.wait(timeout=5)
        return journal.claim(account, {**base, "part": 0, "claim": token})["state"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f1, f2 = pool.submit(attempt, j1, "A" * 32), pool.submit(attempt, j2, "B" * 32)
        assert {f1.result(timeout=10), f2.result(timeout=10)} == {"claimed", "sending"}
    first.close()
    second.close()


def test_account_deletion_cascades_journal(tmp_path):
    from nanomuse_cloud.db import Database
    from nanomuse_cloud.delivery_journal import DeliveryJournal

    db = Database(str(tmp_path / "cloud.sqlite3"))
    account = str(db.create_account("id-hash", "email", "hint", 0)["id"])
    body = {"mid": str(uuid.uuid4()), "channel": "max", "chat": "chat", "chunks": [digest("part")]}
    DeliveryJournal(db).prepare(account, body)
    db.delete_account(account)
    with db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM delivery_journal").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM delivery_journal_parts").fetchone()[0] == 0
    db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["telegram", "max"])
@pytest.mark.parametrize("lost_at", ["claim", "finish"])
async def test_two_bridge_adapters_use_shared_relay_after_lost_response(channel, lost_at, tmp_path, monkeypatch):
    from types import SimpleNamespace

    import httpx
    from nanomuse.channels.cloud_messenger_adapter import CloudMessengerAdapter

    app, client, sender, _up, cloud, _settings = make()
    key_one = await signed_in(client, sender)
    key_two = (await sign_up(client, sender, identifier="13800138000", device="other"))["api_key"]
    account = cloud.authenticate(key_one).account_id
    real_client = httpx.AsyncClient
    lose_response = True

    class RelayClient:
        def __init__(self, **_kwargs):
            self.client = real_client(transport=httpx.ASGITransport(app=app), base_url="http://relay.test")

        async def __aenter__(self):
            await self.client.__aenter__()
            return self

        async def __aexit__(self, *args):
            return await self.client.__aexit__(*args)

        async def post(self, url, **kwargs):
            nonlocal lose_response
            response = await self.client.post(url, **kwargs)
            if url.endswith("/v1/delivery/" + lost_at) and lose_response:
                lose_response = False
                raise httpx.ReadTimeout("the committed relay response was lost")
            return response

    calls = []

    class Platform:
        async def _say(self, chat, part, reply):
            calls.append((chat, part.text))
            return {"message_id": 123}

        async def _send_part(self, chat, part, reply):
            calls.append((chat, part.text))
            return "123"

    def adapter(path):
        return CloudMessengerAdapter(SimpleNamespace(svc=SimpleNamespace(data_dir=path), channels={channel: Platform()}))

    a, b = adapter(tmp_path / "device-one"), adapter(tmp_path / "device-two")

    async def id_one(_msg):
        return account, key_one, "http://relay.test", "unused"

    async def id_two(_msg):
        return account, key_two, "http://relay.test", "unused"

    a._identity, b._identity = id_one, id_two
    monkeypatch.setattr(httpx, "AsyncClient", RelayClient)
    job = {"account": account, "channel": channel, "sender": "sender", "chat": "chat", "message": "11",
           "operation": "operation", "response_mid": str(uuid.uuid4())}
    chunks = a._parts("The response")
    await a._prepare_delivery(job, chunks)

    async def a_send(i, chunk):
        return await a._send_part(job, i, chunk)

    async def b_send(i, chunk):
        return await b._send_part(job, i, chunk)

    args = (account, channel, "sender", "chat", "operation", chunks)
    assert await a.bridge.delivery.deliver(*args, a_send) == {"state": "unknown", "sent": 0}
    await b._prepare_delivery(job, chunks)
    result = await b.bridge.delivery.deliver(*args, b_send)
    if lost_at == "finish":
        assert result == {"state": "sent", "sent": 1}
        assert calls == [("chat", "The response")]
    else:
        # The first process never got permission to send; relay may still have
        # committed the claim, so nobody is allowed to guess and send again.
        assert result == {"state": "in_progress", "sent": 0}
        assert calls == []


@pytest.mark.asyncio
async def test_explicit_retry_is_account_scoped_and_decision_cannot_reset_later_attempt():
    _app, client, sender, _up, _cloud, _settings = make()
    first = await signed_in(client, sender)
    other = await signed_in(client, sender, identifier="13800138001")
    headers = auth(first)
    base = {"mid": str(uuid.uuid4()), "channel": "telegram", "chat": "1|2", "chunks": [digest("a")]}
    assert (await client.post("/v1/delivery/prepare", json=base, headers=headers)).status_code == 200
    old = {**base, "part": 0, "claim": "A" * 32}
    assert (await client.post("/v1/delivery/claim", json=old, headers=headers)).json()["state"] == "claimed"
    decision = {**base, "part": 0, "decision": "B" * 32}
    assert (await client.post("/v1/delivery/retry", json=decision, headers=auth(other))).status_code == 404
    assert (await client.post("/v1/delivery/retry", json={**decision, "chat": "1|3"}, headers=headers)).status_code == 404
    assert (await client.post("/v1/delivery/retry", json=decision, headers=headers)).json()["state"] == "authorized"
    new = {**base, "part": 0, "claim": "C" * 32}
    assert (await client.post("/v1/delivery/claim", json=new, headers=headers)).json()["state"] == "claimed"
    late = await client.post("/v1/delivery/finish", json={**old, "state": "sent", "remote_id": "old"}, headers=headers)
    assert late.status_code == 409
    assert (await client.post("/v1/delivery/retry", json=decision, headers=headers)).json()["state"] == "authorized"
    assert (await client.post("/v1/delivery/claim", json={**new, "claim": "D" * 32}, headers=headers)).json()["state"] == "sending"
    assert (await client.post("/v1/delivery/finish", json={**new, "state": "sent", "remote_id": "new"}, headers=headers)).status_code == 200
    assert (await client.post("/v1/delivery/retry", json=decision, headers=headers)).json()["state"] == "authorized"
    assert (await client.post("/v1/delivery/retry", json={**decision, "decision": "E" * 32}, headers=headers)).status_code == 409
    assert (await client.post("/v1/delivery/claim", json={**new, "claim": "F" * 32}, headers=headers)).json()["state"] == "sent"


@pytest.mark.asyncio
async def test_account_quotas_fail_closed_without_breaking_existing_deliveries(monkeypatch):
    from nanomuse_cloud import delivery_journal

    monkeypatch.setattr(delivery_journal, "MAX_ACCOUNT_DELIVERIES", 2)
    monkeypatch.setattr(delivery_journal, "MAX_ACCOUNT_PARTS", 3)
    _app, client, sender, _up, _cloud, _settings = make()
    one = await signed_in(client, sender)
    two = await signed_in(client, sender, identifier="13800138001")
    first = {"mid": str(uuid.uuid4()), "channel": "telegram", "chat": "1", "chunks": [digest("a"), digest("b")]}
    next_message = {**first, "mid": str(uuid.uuid4()), "chunks": [digest("c")]}
    over = {**next_message, "mid": str(uuid.uuid4())}
    assert (await client.post("/v1/delivery/prepare", json=first, headers=auth(one))).status_code == 200
    assert (await client.post("/v1/delivery/prepare", json=next_message, headers=auth(one))).status_code == 200
    blocked = await client.post("/v1/delivery/prepare", json=over, headers=auth(one))
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "delivery_journal_full"
    # Existing work can still be read and completed, and another account is independent.
    assert (await client.post("/v1/delivery/prepare", json=first, headers=auth(one))).status_code == 200
    assert (await client.post("/v1/delivery/prepare", json=over, headers=auth(two))).status_code == 200
    claim = {**first, "part": 0, "claim": "Q" * 32}
    assert (await client.post("/v1/delivery/claim", json=claim, headers=auth(one))).json()["state"] == "claimed"
    assert (await client.post("/v1/delivery/finish", json={**claim, "state": "sent", "remote_id": "id"}, headers=auth(one))).status_code == 200


@pytest.mark.asyncio
async def test_decision_quota_keeps_existing_confirmation_idempotent(monkeypatch):
    from nanomuse_cloud import delivery_journal

    monkeypatch.setattr(delivery_journal, "MAX_DECISIONS_PER_DELIVERY", 1)
    _app, client, sender, _up, _cloud, _settings = make()
    key = await signed_in(client, sender)
    headers = auth(key)
    base = {"mid": str(uuid.uuid4()), "channel": "max", "chat": "1", "chunks": [digest("a")]}
    await client.post("/v1/delivery/prepare", json=base, headers=headers)
    claim = {**base, "part": 0, "claim": "A" * 32}
    await client.post("/v1/delivery/claim", json=claim, headers=headers)
    decision = {**base, "part": 0, "decision": "B" * 32}
    assert (await client.post("/v1/delivery/retry", json=decision, headers=headers)).status_code == 200
    assert (await client.post("/v1/delivery/retry", json=decision, headers=headers)).status_code == 200
    blocked = await client.post("/v1/delivery/retry", json={**decision, "decision": "C" * 32}, headers=headers)
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "delivery_decisions_full"


def _global_quota_contender(path, account, barrier, result):
    """Independent process and connection; the transaction must arbitrate quota."""
    from nanomuse_cloud import delivery_journal as module
    from nanomuse_cloud.db import Database
    from nanomuse_cloud.service import CloudError

    module.MAX_TOTAL_DELIVERIES = 1
    db = Database(path)
    journal = module.DeliveryJournal(db)
    try:
        barrier.wait(timeout=10)
        journal.prepare(account, {
            "mid": str(uuid.uuid4()), "channel": "max", "chat": "1", "chunks": [digest("a")],
        })
        result.put("created")
    except CloudError as exc:
        result.put(exc.code)
    finally:
        db.close()


def test_delivery_global_quota_is_atomic_across_processes_and_accounts(tmp_path):
    import multiprocessing

    from nanomuse_cloud.db import Database
    from nanomuse_cloud.delivery_journal import DeliveryJournal

    path = str(tmp_path / "cloud.sqlite3")
    db = Database(path)
    a = str(db.create_account("hash-a", "email", "a", 0)["id"])
    b = str(db.create_account("hash-b", "email", "b", 0)["id"])
    DeliveryJournal(db)
    db.close()
    ctx = multiprocessing.get_context("spawn")
    barrier, result = ctx.Barrier(2), ctx.Queue()
    processes = [ctx.Process(target=_global_quota_contender, args=(path, account, barrier, result)) for account in (a, b)]
    for process in processes:
        process.start()
    outcomes = [result.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    assert sorted(outcomes) == ["created", "delivery_journal_full"]
    reopened = Database(path)
    DeliveryJournal(reopened)
    with reopened.tx() as c:
        assert tuple(c.execute("SELECT deliveries,parts FROM delivery_journal_totals").fetchone()) == (1, 1)
        assert sum(int(r[0]) for r in c.execute("SELECT deliveries FROM delivery_journal_usage")) == 1
    reopened.close()


def test_counter_migration_is_idempotent_and_delete_releases_global_quota(tmp_path, monkeypatch):
    from nanomuse_cloud import delivery_journal as module
    from nanomuse_cloud.db import Database
    from nanomuse_cloud.service import CloudError

    path = str(tmp_path / "cloud.sqlite3")
    db = Database(path)
    a = str(db.create_account("hash-a", "email", "a", 0)["id"])
    b = str(db.create_account("hash-b", "email", "b", 0)["id"])
    module.DeliveryJournal(db)
    # Simulate a pre-counter database with existing journal data.
    with db.tx() as c:
        c.execute("DROP TRIGGER delivery_journal_count_insert")
        c.execute("DROP TRIGGER delivery_journal_count_delete")
        c.execute("DROP TABLE delivery_journal_usage")
        c.execute("DROP TABLE delivery_journal_totals")
        c.execute("INSERT INTO delivery_journal VALUES (?,?,?,?,?,?)", (a, str(uuid.uuid4()), "max", "target", "d", 2))
    journal = module.DeliveryJournal(db)
    module.DeliveryJournal(db)
    with db.tx() as c:
        assert tuple(c.execute("SELECT deliveries,parts FROM delivery_journal_totals").fetchone()) == (1, 2)
        assert tuple(c.execute("SELECT deliveries,parts FROM delivery_journal_usage WHERE account_id=?", (a,)).fetchone()) == (1, 2)
    monkeypatch.setattr(module, "MAX_TOTAL_PARTS", 2)
    payload = {"mid": str(uuid.uuid4()), "channel": "max", "chat": "1", "chunks": [digest("part")]}
    with pytest.raises(CloudError) as blocked:
        journal.prepare(b, payload)
    assert blocked.value.code == "delivery_journal_full"
    # Explicit account deletion, unlike an implicit retention sweep, is authoritative.
    db.delete_account(a)
    journal.prepare(b, payload)
    with db.tx() as c:
        assert tuple(c.execute("SELECT deliveries,parts FROM delivery_journal_totals").fetchone()) == (1, 1)
        assert c.execute("SELECT COUNT(*) FROM delivery_journal_usage WHERE account_id=?", (a,)).fetchone()[0] == 0
    db.close()
