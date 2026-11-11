"""The relay keeps only verified, immutable parent links within an account chat."""

from __future__ import annotations

import uuid

from test_accounts import auth, make
from test_cloud import sign_up

from nanomuse_cloud.db import Database


def uid() -> str:
    return str(uuid.uuid4())


def conv(cid: str) -> dict:
    return {"cid": cid, "kind": "side", "title": "Chat", "created_at": 1, "updated_at": 1}


def msg(mid: str, cid: str, role: str = "user", text: str = "hello", **extra) -> dict:
    return {"mid": mid, "cid": cid, "role": role, "text": text, "created_at": 1, **extra}


async def signed_in(client, sender, identifier="13800138000", device="pixel") -> str:
    key = (await sign_up(client, sender, identifier, device))["api_key"]
    await client.put("/v1/sync/state", json={"enabled": True}, headers=auth(key))
    return key


async def test_reply_link_survives_cross_device_reads_and_cannot_be_changed():
    _app, client, sender, _up, _cloud, _settings = make()
    key_a = await signed_in(client, sender)
    key_b = (await sign_up(client, sender, "13800138000", "other-device"))["api_key"]
    cid, first, second, answer = uid(), uid(), uid(), uid()
    response = await client.post(
        "/v1/sync/changes", headers=auth(key_a),
        json={"device": "device-a", "conversations": [conv(cid)], "messages": [
            msg(first, cid, "user", "A"), msg(second, cid, "user", "B"),
            msg(answer, cid, "assistant", "answer A", reply_to_mid=first),
        ]},
    )
    assert response.status_code == 200 and response.json()["accepted"] == 4
    changes = (await client.get("/v1/sync/changes?since=0", headers=auth(key_b))).json()
    row = next(item for item in changes["messages"] if item["mid"] == answer)
    assert row["reply_to_mid"] == first
    page = (await client.get(f"/v1/sync/conversations/{cid}/messages", headers=auth(key_b))).json()
    assert next(item for item in page["messages"] if item["mid"] == answer)["reply_to_mid"] == first
    replay = await client.post(
        "/v1/sync/changes", headers=auth(key_b),
        json={"device": "device-b", "messages": [msg(answer, cid, "assistant", "answer A", reply_to_mid=second)]},
    )
    assert replay.json()["rejected"][0]["reason"] == "known_mid"
    row = next(item for item in (await client.get("/v1/sync/changes?since=0", headers=auth(key_a))).json()["messages"] if item["mid"] == answer)
    assert row["reply_to_mid"] == first


async def test_reply_parent_must_be_same_account_conversation_and_user():
    _app, client, sender, _up, _cloud, _settings = make()
    key_a = await signed_in(client, sender)
    key_b = await signed_in(client, sender, "13900139000", "other")
    cid_a, cid_b, other_cid, foreign_user, local_assistant, local_user, other_user = (uid() for _ in range(7))
    await client.post("/v1/sync/changes", headers=auth(key_a), json={"device": "a", "conversations": [conv(cid_a), conv(other_cid)], "messages": [msg(local_user, cid_a), msg(local_assistant, cid_a, "assistant"), msg(other_user, other_cid)]})
    await client.post("/v1/sync/changes", headers=auth(key_b), json={"device": "b", "conversations": [conv(cid_b)], "messages": [msg(foreign_user, cid_b)]})
    cases = [(foreign_user, "assistant", "reply_parent_missing"), (other_user, "assistant", "reply_parent_missing"), (local_assistant, "assistant", "reply_parent_missing"), (local_user, "user", "bad_reply_parent"), ("bad id", "assistant", "bad_reply_parent")]
    for parent, role, reason in cases:
        result = await client.post("/v1/sync/changes", headers=auth(key_a), json={"device": "a", "messages": [msg(uid(), cid_a, role, reply_to_mid=parent)]})
        assert result.json()["rejected"][0]["reason"] == reason
    # Old clients and old messages still work without the optional field.
    legacy = await client.post("/v1/sync/changes", headers=auth(key_a), json={"device": "a", "messages": [msg(uid(), cid_a, "assistant")]})
    assert legacy.json()["accepted"] == 1


async def test_unordered_batch_waits_for_parent_and_deleted_parent_is_not_reused():
    _app, client, sender, _up, _cloud, _settings = make()
    key = await signed_in(client, sender)
    cid, parent, answer = uid(), uid(), uid()
    row = msg(answer, cid, "assistant", "answer", reply_to_mid=parent)
    first = await client.post(
        "/v1/sync/changes", headers=auth(key),
        json={"device": "a", "conversations": [conv(cid)], "messages": [row, msg(parent, cid)]},
    )
    assert first.json()["rejected"][0]["reason"] == "reply_parent_missing"
    retry = await client.post("/v1/sync/changes", headers=auth(key), json={"device": "a", "messages": [row]})
    assert retry.json()["accepted"] == 1
    await client.post("/v1/sync/changes", headers=auth(key), json={"device": "a", "messages": [msg(parent, cid, deleted=True)]})
    refused = await client.post("/v1/sync/changes", headers=auth(key), json={"device": "a", "messages": [msg(uid(), cid, "assistant", reply_to_mid=parent)]})
    assert refused.json()["rejected"][0]["reason"] == "reply_parent_missing"


def test_existing_database_migrates_without_guessing_old_links(tmp_path):
    path = str(tmp_path / "relay.sqlite")
    db = Database(path)
    db._conn.execute("INSERT INTO accounts(id,id_hash,channel,hint,created_at) VALUES ('a','h','email','a',1)")
    db._conn.execute("INSERT INTO sync_messages(account_id,mid,cid,seq,device,role,text,created_at) VALUES ('a','old','chat',1,'pc','assistant','old text',1)")
    db._conn.execute("ALTER TABLE sync_messages DROP COLUMN reply_to_mid")
    db.close()
    migrated = Database(path)
    assert "sync_messages.reply_to_mid" in migrated.added
    cols = {row["name"] for row in migrated._conn.execute("PRAGMA table_info(sync_messages)")}
    assert "reply_to_mid" in cols
    row = migrated._conn.execute("SELECT text,reply_to_mid FROM sync_messages WHERE mid='old'").fetchone()
    assert tuple(row) == ("old text", "")
    migrated.close()
