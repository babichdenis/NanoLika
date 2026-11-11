"""End-to-end journal, scoped picker and delivery across restart/retry."""

from __future__ import annotations

import pytest

from nanomuse.channels.account_picker import CatalogUnavailable, ConversationUnavailable
from nanomuse.channels.cloud_messenger import CloudMessengerBridge, JobConflict


def catalog(account, archived=False):
    return {"account_id": account, "complete": True, "conversations": [
        {"cid": "same-cid", "archived": archived}, {"cid": "other-cid", "archived": False},
    ]}


@pytest.mark.asyncio
async def test_two_accounts_resume_and_deliver_only_their_own_answer(tmp_path):
    bridge = CloudMessengerBridge(tmp_path)
    for account, sender in (("a", "100"), ("b", "200")):
        bridge.picker.choose(account, "telegram", sender, sender + ":1", "same-cid", catalog(account))
    a = bridge.record("a", "telegram", "100", "100:1", "message-1", "question-a", catalog("a"))
    b = bridge.record("b", "telegram", "200", "200:1", "message-1", "question-b", catalog("b"))
    assert a["operation"] != b["operation"]
    calls, sent = [], []

    async def send_request(job):
        calls.append(job["operation"])
        return {"state": "accepted"}

    async def pending(_job):
        return {"state": "pending"}

    async def done(job):
        return {"state": "completed", "text": "answer-" + job["account"]}

    async def send_part(job, index, chunk):
        sent.append((job["account"], job["chat"], index, chunk))
        return "platform-id"

    assert await bridge.advance(a, catalog("a"), send_request, pending, lambda s: [s], send_part) == "accepted"
    # After restart, accepted operations poll rather than submit a second time.
    bridge = CloudMessengerBridge(tmp_path)
    resumed = next(job for job in bridge.pending() if job["account"] == "a")
    assert await bridge.advance(resumed, catalog("a"), send_request, done, lambda s: [s], send_part) == "done"
    assert await bridge.advance(b, catalog("b"), send_request, done, lambda s: [s], send_part) == "done"
    assert sent == [("a", "100:1", 0, "answer-a"), ("b", "200:1", 0, "answer-b")]
    assert len(calls) == 2 and not bridge.pending()
    again = bridge.record("a", "telegram", "100", "100:1", "message-1", "question-a", catalog("a"))
    assert await bridge.advance(again, catalog("a"), send_request, done, lambda s: [s], send_part) == "done"
    assert len(sent) == 2


@pytest.mark.asyncio
async def test_uncertain_delivery_is_never_retried_and_disabled_sync_keeps_binding(tmp_path):
    bridge = CloudMessengerBridge(tmp_path)
    bridge.picker.choose("a", "max", "1", "max-chat", "same-cid", catalog("a"))
    job = bridge.record("a", "max", "1", "max-chat", "mid-1", "question", catalog("a"))

    async def accepted(_job):
        return {"state": "accepted"}

    async def done(_job):
        return {"state": "completed", "text": "answer"}

    attempts = []

    async def unknown(_job, _index, _chunk):
        attempts.append(True)
        raise TimeoutError("platform may have accepted")

    with pytest.raises(CatalogUnavailable):
        await bridge.advance(job, {}, accepted, done, lambda s: [s], unknown)
    with pytest.raises(ConversationUnavailable):
        await bridge.advance(job, catalog("a", archived=True), accepted, done, lambda s: [s], unknown)
    assert await bridge.advance(job, catalog("a"), accepted, done, lambda s: [s], unknown) == "unknown"
    restarted = CloudMessengerBridge(tmp_path)
    assert not restarted.pending()
    same = restarted.record("a", "max", "1", "max-chat", "mid-1", "question", catalog("a"))
    assert await restarted.advance(same, catalog("a"), accepted, done, lambda s: [s], unknown) == "unknown"
    assert len(attempts) == 1
    with pytest.raises(JobConflict):
        restarted.record("a", "max", "1", "max-chat", "mid-1", "changed", catalog("a"))


@pytest.mark.asyncio
async def test_concurrent_advances_do_not_freeze_a_successful_delivery(tmp_path):
    import asyncio

    bridge = CloudMessengerBridge(tmp_path)
    bridge.picker.choose("a", "telegram", "1", "1:2", "same-cid", catalog("a"))
    job = bridge.record("a", "telegram", "1", "1:2", "mid-1", "question", catalog("a"))

    async def accepted(_job):
        return {"state": "accepted"}

    async def done(_job):
        return {"state": "completed", "text": "answer"}

    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def send_part(_job, _index, _chunk):
        calls.append(True)
        started.set()
        await release.wait()
        return "platform-id"

    first = asyncio.create_task(bridge.advance(job, catalog("a"), accepted, done, lambda s: [s], send_part))
    await started.wait()
    # A second caller sees the in-flight part and must not mark the job unknown.
    current = next(iter(bridge.pending()))
    assert await bridge.advance(current, catalog("a"), accepted, done, lambda s: [s], send_part) == "accepted"
    release.set()
    assert await first == "done"
    assert not bridge.pending() and len(calls) == 1


@pytest.mark.asyncio
async def test_archiving_while_task_runs_blocks_delivery_without_erasing_job(tmp_path):
    bridge = CloudMessengerBridge(tmp_path)
    bridge.picker.choose("a", "telegram", "1", "1:2", "same-cid", catalog("a"))
    job = bridge.record("a", "telegram", "1", "1:2", "message-1", "question", catalog("a"))
    delivered = []

    async def accepted(_job):
        return {"state": "accepted"}

    async def pending(_job):
        return {"state": "pending"}

    async def done(_job):
        return {"state": "completed", "text": "answer"}

    async def send_part(_job, _index, chunk):
        delivered.append(chunk)
        return "platform-id"

    assert await bridge.advance(job, catalog("a"), accepted, pending, lambda s: [s], send_part) == "accepted"
    current = bridge.pending()[0]
    with pytest.raises(ConversationUnavailable):
        await bridge.advance(current, catalog("a", archived=True), accepted, done, lambda s: [s], send_part)
    assert not delivered and bridge.pending()[0]["state"] == "accepted"
    assert await bridge.advance(current, catalog("a"), accepted, done, lambda s: [s], send_part) == "done"
    assert delivered == ["answer"]


def test_jobs_limit_fails_closed_without_erasing_existing_request(tmp_path, monkeypatch):
    from nanomuse.channels.cloud_messenger import StorageFull

    bridge = CloudMessengerBridge(tmp_path)
    bridge.picker.choose("a", "max", "1", "chat", "same-cid", catalog("a"))
    monkeypatch.setattr("nanomuse.channels.cloud_messenger.MAX_ACCOUNT_JOBS", 1)
    first = bridge.record("a", "max", "1", "chat", "mid-1", "first", catalog("a"))
    with pytest.raises(StorageFull):
        bridge.record("a", "max", "1", "chat", "mid-2", "second", catalog("a"))
    assert [row["operation"] for row in bridge.pending()] == [first["operation"]]


@pytest.mark.asyncio
async def test_full_relay_delivery_journal_blocks_send_without_retry(tmp_path):
    from nanomuse.channels.cloud_messenger import DeliveryJournalFull

    bridge = CloudMessengerBridge(tmp_path)
    bridge.picker.choose("a", "max", "1", "chat", "same-cid", catalog("a"))
    job = bridge.record("a", "max", "1", "chat", "mid-1", "question", catalog("a"))
    sends = []

    async def accepted(_job):
        return {"state": "accepted"}

    async def done(_job):
        return {"state": "completed", "text": "answer", "mid": "response-mid"}

    async def full(_job, _chunks):
        raise DeliveryJournalFull()

    async def send_part(*_args):
        sends.append(True)
        return "id"

    assert await bridge.advance(job, catalog("a"), accepted, done, lambda s: [s], send_part, prepare=full) == "delivery_full"
    assert not sends and not bridge.pending()
    assert bridge.recovery_jobs("a", "max", "1", "chat")[0]["state"] == "delivery_full"


def test_identical_messages_in_different_topics_never_share_operation(tmp_path):
    bridge = CloudMessengerBridge(tmp_path)
    bridge.picker.choose("a", "telegram", "1", "1|10", "same-cid", catalog("a"))
    bridge.picker.choose("a", "telegram", "1", "1|20", "other-cid", catalog("a"))
    first = bridge.record("a", "telegram", "1", "1|10", "same-id", "same text", catalog("a"))
    second = bridge.record("a", "telegram", "1", "1|20", "same-id", "same text", catalog("a"))
    assert first["operation"] != second["operation"]
    assert first["conversation"] == "same-cid" and second["conversation"] == "other-cid"


@pytest.mark.asyncio
@pytest.mark.parametrize("relay_state,expected", [("sent", "done"), ("sending", "unknown")])
async def test_restart_reconciles_relay_before_retrying_a_sending_part(tmp_path, relay_state, expected):
    bridge = CloudMessengerBridge(tmp_path)
    bridge.picker.choose("a", "max", "1", "chat", "same-cid", catalog("a"))
    job = bridge.record("a", "max", "1", "chat", "message-1", "question", catalog("a"))
    bridge._transition(job["operation"], "queued", "accepted")
    chunks = ["answer"]
    bridge.delivery.prepare("a", "max", "1", "chat", job["operation"], chunks)
    assert bridge.delivery._next("a", "max", job["operation"], "old-claim")[0] == "send"
    bridge = CloudMessengerBridge(tmp_path)
    job = bridge.pending()[0]

    async def unused(_job):
        raise AssertionError("must not submit the request again")

    async def result(_job):
        return {"state": "completed", "text": "answer", "mid": "response-mid"}

    async def prepare(_job, _chunks):
        return {"parts": [{"part": 0, "state": relay_state, "remote_id": "platform-id" if relay_state == "sent" else ""}]}

    async def never_send(*_args):
        raise AssertionError("an ambiguous/already sent part cannot be repeated")

    assert await bridge.advance(job, catalog("a"), unused, result, lambda s: [s], never_send, prepare=prepare) == expected
    assert not bridge.pending()
    if expected == "unknown":
        assert bridge.recovery_jobs("a", "max", "1", "chat")[0]["state"] == "unknown"


def test_due_scheduler_is_bounded_fair_and_persists_backoff(tmp_path, monkeypatch):
    import sqlite3

    bridge = CloudMessengerBridge(tmp_path)
    with sqlite3.connect(bridge.path) as db:
        db.executemany(
            "INSERT INTO jobs(operation,account,channel,sender,chat,message,conversation,text,state) "
            "VALUES(?,?,?,?,?,?,?,?, 'queued')",
            [(f"op-{account}-{n}", f"account-{account}", "max", str(account), "chat", str(n), "cid", "text")
             for account in range(100) for n in range(50)],
        )
    batches = [bridge.claim_due(20) for _ in range(5)]
    assert all(len(batch) == 20 for batch in batches)
    assert len({job["account"] for batch in batches for job in batch}) == 100
    assert len(bridge.pending(10000)) == 20
    clock = [1000.0]
    monkeypatch.setattr("nanomuse.channels.cloud_messenger.time.time", lambda: clock[0])
    job = batches[0][0]
    bridge.defer(job["operation"], failed=True)
    with sqlite3.connect(bridge.path) as db:
        attempts, next_attempt = db.execute("SELECT attempts,next_attempt FROM jobs WHERE operation=?", (job["operation"],)).fetchone()
    assert attempts == 1 and next_attempt == 1010.0
    restarted = CloudMessengerBridge(tmp_path)
    with sqlite3.connect(restarted.path) as db:
        assert db.execute("SELECT attempts,next_attempt FROM jobs WHERE operation=?", (job["operation"],)).fetchone() == (1, 1010.0)
