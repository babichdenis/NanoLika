"""Shared metadata, lazy bodies, read races and account boundaries."""

import httpx
import pytest
from test_accounts import auth, make
from test_cloud import sign_up
from test_sync import conv, msg, uid

from nanomuse_cloud.db import Database


async def setup():
    app, client, sender, _, _, _ = make()
    a = await sign_up(client, sender, "reader@example.test")
    b = await sign_up(client, sender, "other@example.test")
    headers = auth(a["api_key"])
    other = auth(b["api_key"])
    # a new account starts with sync off; these tests are about what a synced store does
    await client.put("/v1/sync/state", json={"enabled": True}, headers=headers)
    await client.put("/v1/sync/state", json={"enabled": True}, headers=other)
    c1, c2 = uid(), uid()
    payload = {
        "device": "computer",
        "conversations": [conv(c1, title="First"), conv(c2, title="Second")],
        "messages": [msg(uid(), c1, text="private first"), msg(uid(), c1, "assistant", "answer"), msg(uid(), c2, text="private second")],
    }
    r = await client.post("/v1/sync/changes", headers=headers, json=payload)
    assert r.status_code == 200, r.text
    return app, client, headers, other, c1, c2


async def test_catalog_has_counts_but_no_bodies_and_is_account_scoped():
    _, client, a, b, c1, c2 = await setup()
    r = await client.get("/v1/sync/catalog?client=telegram-view", headers=a)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    data = r.json()
    rows = {r["cid"]: r for r in data["conversations"]}
    assert rows[c1]["unread"] == 2 and rows[c2]["unread"] == 1
    assert "private" not in r.text and "messages" not in data
    assert (await client.get("/v1/sync/catalog?client=telegram-view", headers=b)).json()["conversations"] == []
    for url in [f"/v1/sync/conversations/{c1}/messages", "/v1/sync/catalog?client=telegram-view"]:
        assert (await client.get(url)).status_code == 401
    assert (await client.get(f"/v1/sync/conversations/{c1}/messages", headers=b)).status_code == 404
    assert (await client.post(f"/v1/sync/conversations/{c1}/read", headers=b, json={"client": "view", "through_seq": 0})).status_code == 404


async def test_read_marks_are_per_client_and_monotone_and_do_not_swallow_later_messages():
    _, client, a, _, c1, c2 = await setup()
    page = (await client.get(f"/v1/sync/conversations/{c1}/messages", headers=a)).json()
    assert len(page["messages"]) == 2 and all(m["cid"] == c1 for m in page["messages"])
    through = page["through_seq"]
    await client.post("/v1/sync/changes", headers=a, json={"device": "computer", "messages": [msg(uid(), c1, text="later")]})
    mark = await client.post(f"/v1/sync/conversations/{c1}/read", headers=a, json={"client": "telegram-view", "through_seq": through})
    assert mark.status_code == 200, mark.text
    await client.post(f"/v1/sync/conversations/{c1}/read", headers=a, json={"client": "telegram-view", "through_seq": 0})
    tg = (await client.get("/v1/sync/catalog?client=telegram-view", headers=a)).json()
    assert next(r for r in tg["conversations"] if r["cid"] == c1)["unread"] == 1
    desktop = (await client.get("/v1/sync/catalog?client=desktop-view", headers=a)).json()
    assert next(r for r in desktop["conversations"] if r["cid"] == c1)["unread"] == 3
    own = (await client.get("/v1/sync/catalog?client=desktop-view&device=computer", headers=a)).json()
    assert {r["cid"]: r["unread"] for r in own["conversations"]} == {c1: 1, c2: 0}
    assert (
        await client.post(f"/v1/sync/conversations/{c1}/read", headers=a, json={"client": "telegram-view", "through_seq": 10**12})
    ).status_code == 400


async def test_open_pages_only_requested_chat_and_supports_older_history():
    _, client, a, _, c1, c2 = await setup()
    page = (await client.get(f"/v1/sync/conversations/{c1}/messages?limit=1", headers=a)).json()
    assert len(page["messages"]) == 1 and page["has_older"] and not page["more"]
    seq = page["messages"][0]["seq"]
    older = (await client.get(f"/v1/sync/conversations/{c1}/messages?before={seq}", headers=a)).json()
    assert len(older["messages"]) == 1 and older["messages"][0]["seq"] < seq
    updates = (await client.get(f"/v1/sync/conversations/{c1}/messages?after=1&limit=1", headers=a)).json()
    assert updates["more"] and len(updates["messages"]) == 1
    assert (await client.get(f"/v1/sync/conversations/{c1}/messages?before=1&after=1", headers=a)).status_code == 400
    await client.delete(f"/v1/sync/conversations/{c1}", headers=a)
    assert (await client.get(f"/v1/sync/conversations/{c1}/messages", headers=a)).status_code == 404
    assert [r["cid"] for r in (await client.get("/v1/sync/catalog?client=view", headers=a)).json()["conversations"]] == [c2]


async def test_sync_off_and_reader_validation():
    _, client, a, _, c1, _ = await setup()
    assert (await client.get("/v1/sync/catalog?client=bad%20reader", headers=a)).status_code == 422
    assert (
        await client.post(f"/v1/sync/conversations/{c1}/read", headers=a, json={"client": "view", "through_seq": True})
    ).status_code == 422
    await client.put("/v1/sync/state", headers=a, json={"enabled": False})
    assert (await client.get("/v1/sync/catalog?client=view", headers=a)).status_code == 409


async def test_wiping_server_copy_cleans_read_marks_without_fabricating_tombstones():
    app, client, a, _, c1, _ = await setup()
    page = (await client.get(f"/v1/sync/conversations/{c1}/messages", headers=a)).json()
    await client.post(f"/v1/sync/conversations/{c1}/read", headers=a, json={"client": "view", "through_seq": page["through_seq"]})
    await client.delete("/v1/sync/changes", headers=a)
    catalog = (await client.get("/v1/sync/catalog?client=view", headers=a)).json()
    assert catalog["conversations"] == [] and catalog["deleted"] == []
    assert app.state.sync.db._conn.execute("SELECT COUNT(*) FROM sync_read_marks").fetchone()[0] == 0


async def test_archive_can_hide_and_restore_conversation_without_losing_text():
    _, client, a, _, c1, _ = await setup()
    archived = await client.post(f"/v1/sync/conversations/{c1}/archive", headers=a, json={"archived": True})
    assert archived.status_code == 200 and archived.json()["archived"] is True
    row = next(r for r in (await client.get("/v1/sync/catalog?client=view", headers=a)).json()["conversations"] if r["cid"] == c1)
    assert row["archived"] is True and row["message_count"] == 2
    page = await client.get(f"/v1/sync/conversations/{c1}/messages", headers=a)
    assert [m["text"] for m in page.json()["messages"]] == ["private first", "answer"]
    restored = await client.post(f"/v1/sync/conversations/{c1}/archive", headers=a, json={"archived": False})
    assert restored.status_code == 200 and restored.json()["archived"] is False
    assert (
        next(r for r in (await client.get("/v1/sync/catalog?client=view", headers=a)).json()["conversations"] if r["cid"] == c1)["archived"]
        is False
    )


async def test_daily_feed_conversations_fold_into_one_lenta_without_losing_messages():
    _, client, a, _, _, _ = await setup()
    feed1, feed2 = uid(), uid()
    await client.post(
        "/v1/sync/changes",
        headers=a,
        json={
            "device": "computer",
            "conversations": [
                conv(feed1, title="Feed \u00b7 sunday, 25 october 2026"),
                conv(feed2, title="Feed \u00b7 monday, 26 october 2026"),
            ],
            "messages": [msg(uid(), feed1, text="First note"), msg(uid(), feed2, text="Second note")],
        },
    )
    data = (await client.get("/v1/sync/catalog?client=view", headers=a)).json()
    feeds = [r for r in data["conversations"] if r["title"] == "\u041b\u0435\u043d\u0442\u0430"]
    assert len(feeds) == 1 and feeds[0]["archived"] is False and feeds[0]["message_count"] == 2
    page = (await client.get(f"/v1/sync/conversations/{feeds[0]['cid']}/messages", headers=a)).json()
    assert {m["text"] for m in page["messages"]} == {"First note", "Second note"}
    assert feeds[0]["cid"] in {feed1, feed2}


async def test_catalog_is_read_only_and_does_not_move_the_cursor():
    app, client, a, _, _, _ = await setup()
    store = app.state.sync
    account = app.state.cloud.authenticate(a["Authorization"].split(" ", 1)[1]).account_id
    before = (await client.get("/v1/sync/catalog?client=view", headers=a)).json()
    with store.db.tx() as c:
        seq_before = store._cursor(c, account)
        rows_before = c.execute("SELECT COUNT(*) FROM sync_messages WHERE account_id=?", (account,)).fetchone()[0]
    after = (await client.get("/v1/sync/catalog?client=view", headers=a)).json()
    with store.db.tx() as c:
        seq_after = store._cursor(c, account)
        rows_after = c.execute("SELECT COUNT(*) FROM sync_messages WHERE account_id=?", (account,)).fetchone()[0]
    assert before["cursor"] == after["cursor"] == seq_before == seq_after
    assert rows_before == rows_after
    assert [row["cid"] for row in before["conversations"]] == [row["cid"] for row in after["conversations"]]


async def test_fold_moves_messages_with_a_new_seq_and_tombstones_the_source():
    app, client, a, _, _, _ = await setup()
    store = app.state.sync
    feed1, feed2 = uid(), uid()
    saved = store._fold_daily_feed
    store._fold_daily_feed = lambda c, account_id: 0
    try:
        await client.post(
            "/v1/sync/changes",
            headers=a,
            json={
                "device": "computer",
                "conversations": [
                    conv(feed1, title="Feed · sunday, 25 october 2026"),
                    conv(feed2, title="Feed · monday, 26 october 2026"),
                ],
                "messages": [msg(uid(), feed1, text="First note"), msg(uid(), feed2, text="Second note")],
            },
        )
    finally:
        store._fold_daily_feed = saved
    before = (await client.get("/v1/sync/changes?since=0", headers=a)).json()
    old_cursor = before["cursor"]
    before_seq = {m["text"]: m["seq"] for m in before["messages"] if m["text"] in {"First note", "Second note"}}
    assert store.fold_all_feeds() == 1
    after = (await client.get("/v1/sync/changes?since=0", headers=a)).json()
    canonical = next(c["cid"] for c in after["conversations"] if c["title"] == "Лента" and not c["deleted"])
    moved = [m for m in after["messages"] if m["text"] in before_seq and m["cid"] == canonical and not m["deleted"]]
    assert len(moved) == 2
    changed = [m for m in moved if m["seq"] != before_seq[m["text"]]]
    assert len(changed) == 1 and changed[0]["seq"] > old_cursor
    assert any(c["cid"] in {feed1, feed2} and c["deleted"] for c in after["conversations"])


async def test_fold_ignores_unrelated_titles():
    _, client, a, _, _, _ = await setup()
    first, second = uid(), uid()
    await client.post(
        "/v1/sync/changes",
        headers=a,
        json={
            "device": "computer",
            "conversations": [conv(first, title="Лента новостей"), conv(second, title="Feed на завтра")],
            "messages": [msg(uid(), first, text="One"), msg(uid(), second, text="Two")],
        },
    )
    data = (await client.get("/v1/sync/catalog?client=view", headers=a)).json()
    assert len([c for c in data["conversations"] if c["title"] in {"Лента новостей", "Feed на завтра"}]) == 2
    assert not any(c["title"] == "Лента" for c in data["conversations"])


async def test_catalog_answers_304_until_something_changes():
    _, client, a, _, c1, _ = await setup()
    first = await client.get("/v1/sync/catalog?client=view", headers=a)
    assert first.status_code == 200 and first.headers["etag"] == f'"{first.json()["rev"]}"'
    etag = first.headers["etag"]
    unchanged = await client.get("/v1/sync/catalog?client=view", headers={**a, "If-None-Match": etag})
    assert unchanged.status_code == 304 and unchanged.content == b""
    assert unchanged.headers["etag"] == etag
    # a new message changes the list (unread and last seq), and with it the tag
    await client.post(
        "/v1/sync/changes",
        headers=a,
        json={"device": "computer", "messages": [msg(uid(), c1, text="new")]},
    )
    changed = await client.get("/v1/sync/catalog?client=view", headers={**a, "If-None-Match": etag})
    assert changed.status_code == 200 and changed.headers["etag"] != etag
    # reading a chat changes only its unread count, which is in the list too
    etag = changed.headers["etag"]
    cursor = changed.json()["cursor"]
    mark = await client.post(
        f"/v1/sync/conversations/{c1}/read",
        headers=a,
        json={"client": "view", "through_seq": cursor},
    )
    assert mark.status_code == 200
    read = await client.get("/v1/sync/catalog?client=view", headers={**a, "If-None-Match": etag})
    assert read.status_code == 200 and read.headers["etag"] != etag
    assert next(row for row in read.json()["conversations"] if row["cid"] == c1)["unread"] == 0

async def test_message_usage_links_all_model_calls_to_their_own_account():
    app, client, sender, _, _, _ = make(rub_per_usd=100, usd_cny=7)
    a = await sign_up(client, sender, "usage@example.test")
    b = await sign_up(client, sender, "other-usage@example.test")
    headers = auth(a["api_key"])
    await client.put("/v1/sync/state", json={"enabled": True}, headers=headers)
    await client.put("/v1/sync/state", json={"enabled": True}, headers=auth(b["api_key"]))
    cid, mid, old_mid = uid(), uid(), uid()
    prompt = {"model": "qwen3.8-27b", "messages": [{"role": "user", "content": "hello"}]}
    for stream in (False, True):
        response = await client.post(
            "/v1/chat/completions", headers={**headers, "X-NanoLika-Message-ID": mid},
            json={**prompt, "stream": stream},
        )
        assert response.status_code == 200, response.text
    await client.post("/v1/sync/changes", headers=headers, json={
        "device": "browser", "conversations": [conv(cid)],
        "messages": [msg(mid, cid, "assistant", "answer"), msg(old_mid, cid, "assistant", "old")],
    })
    result = await client.get(f"/v1/sync/conversations/{cid}/messages", headers=headers)
    assert result.status_code == 200, result.text
    messages = {item["mid"]: item for item in result.json()["messages"]}
    assert messages[mid]["usage"]["requests"] == 2
    assert messages[mid]["usage"]["estimated"] is False
    account_id = app.state.cloud.authenticate(a["api_key"]).account_id
    rows = app.state.cloud.db._conn.execute(
        "SELECT sale_uy FROM ledger WHERE account_id=? AND kind='chat'", (account_id,)
    ).fetchall()
    settings = app.state.cloud.s
    expected = sum(row["sale_uy"] for row in rows) / 1_000_000 / settings.usd_cny * settings.rub_per_usd * (1 + settings.topup_fee_pct / 100)
    assert messages[mid]["usage"]["cost_rub"] == pytest.approx(expected)
    direct = await client.get(f"/v1/me/message-usage/{mid}", headers=headers)
    assert direct.json()["usage"] == messages[mid]["usage"]
    assert direct.headers["cache-control"] == "no-store"
    assert (await client.get(f"/v1/me/message-usage/{mid}", headers=auth(b["api_key"]))).json()["usage"] is None
    assert (await client.get(f"/v1/me/message-usage/{mid}")).status_code == 401
    assert (await client.get("/v1/me/message-usage/invalid", headers=headers)).status_code == 400
    assert messages[mid]["usage"]["input_tokens"] > 0
    assert messages[mid]["usage"]["output_tokens"] > 0
    assert "usage" not in messages[old_mid]
    assert (await client.get(f"/v1/sync/conversations/{cid}/messages", headers=auth(b["api_key"]))).status_code == 404
    other_cid = uid()
    other_headers = auth(b["api_key"])
    await client.post("/v1/sync/changes", headers=other_headers, json={
        "device": "other-browser", "conversations": [conv(other_cid)],
        "messages": [msg(mid, other_cid, "assistant", "same identifier")],
    })
    other_result = await client.get(f"/v1/sync/conversations/{other_cid}/messages", headers=other_headers)
    assert "usage" not in other_result.json()["messages"][0]
    changes = await client.get("/v1/sync/changes?since=0", headers=headers)
    assert any(m.get("usage", {}).get("requests") == 2 for m in changes.json()["messages"])
    await client.aclose()

async def test_message_usage_records_partial_stream_as_estimated():
    app, client, sender, _, _, _ = make(rub_per_usd=100, usd_cny=7)
    signed = await sign_up(client, sender, "partial@example.test")
    headers, mid = auth(signed["api_key"]), uid()

    class Interrupted(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"Partial answer"}}]}\n\n'
            raise httpx.ReadError("connection lost")

    async def provider(request):
        return httpx.Response(200, stream=Interrupted(), headers={"content-type": "text/event-stream"})

    original = app.state.http._transport
    app.state.http._transport = httpx.MockTransport(provider)
    try:
        response = await client.post(
            "/v1/chat/completions", headers={**headers, "X-NanoLika-Message-ID": mid},
            json={"model": "qwen3.8-27b", "messages": [{"role": "user", "content": "hello"}], "stream": True},
        )
        assert response.status_code == 200 and "Partial answer" in response.text
        assert "stopped answering" in response.text
        usage = (await client.get(f"/v1/me/message-usage/{mid}", headers=headers)).json()["usage"]
        assert usage["requests"] == 1 and usage["estimated"] is True
        assert usage["input_tokens"] > 0 and usage["output_tokens"] > 0
    finally:
        app.state.http._transport = original
        await client.aclose()


def test_message_usage_index_migrates_existing_ledger_without_rewriting(tmp_path):
    path = str(tmp_path / "legacy.db")
    db = Database(path)
    db._conn.execute("DROP INDEX ledger_message_usage")
    db._conn.execute("INSERT INTO accounts(id, id_hash, channel, hint, created_at) VALUES('old', 'old', 'email', 'old', 1)")
    db._conn.execute("INSERT INTO ledger(account_id, ts, kind, charged, extra) VALUES('old', 1, 'chat', 0, 'not json')")
    db._conn.commit()
    db._conn.close()
    reopened = Database(path)
    assert reopened._conn.execute("SELECT extra FROM ledger WHERE account_id='old'").fetchone()[0] == "not json"
    assert reopened._conn.execute("SELECT name FROM sqlite_master WHERE name='ledger_message_usage'").fetchone()
    reopened._conn.close()


async def test_message_usage_marks_estimates_and_skips_old_ledger_rows():
    import json

    _, client, sender, _, cloud, _ = make(rub_per_usd=100, usd_cny=7)
    user = await sign_up(client, sender, "estimates@example.test")
    headers = auth(user["api_key"])
    mid = uid()
    account = user["account"]["id"]
    for request_id, extra in (
        ("measured", {"message_mid": mid, "sale_rub_at_charge": 0.00001, "usage_estimated": False}),
        ("estimated", {"message_mid": mid, "sale_rub_at_charge": 0.00002, "usage_estimated": True}),
    ):
        cloud.db.charge(account, "chat", "test-model", 10, 5, 15, request_id, extra=json.dumps(extra))
    cloud.db.charge(account, "chat", "old", 100, 100, 200, "old", extra="not JSON")
    result = await client.get(f"/v1/me/message-usage/{mid}", headers=headers)
    usage = result.json()["usage"]
    assert usage["input_tokens"] == 20 and usage["output_tokens"] == 10
    assert usage["requests"] == 2 and usage["estimated"] is True
    assert abs(usage["cost_rub"] - 0.00003) < 1e-12
    await client.aclose()
