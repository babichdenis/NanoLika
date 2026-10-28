"""Shared metadata, lazy bodies, read races and account boundaries."""

from test_accounts import auth, make
from test_cloud import sign_up
from test_sync import conv, msg, uid


async def setup():
    app, client, sender, _, _, _ = make()
    a = await sign_up(client, sender, "reader@example.test")
    b = await sign_up(client, sender, "other@example.test")
    headers = auth(a["api_key"])
    other = auth(b["api_key"])
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
