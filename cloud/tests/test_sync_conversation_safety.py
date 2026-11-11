"""A message ID cannot be used to alter a different conversation."""

from test_accounts import auth, make
from test_sync import conv, msg, signed_in, uid


async def test_existing_message_cannot_be_deleted_or_claimed_from_another_chat():
    app, client, sender, upstream, cloud, _settings = make()
    try:
        key = await signed_in(client, sender)
        first, second, mid = uid(), uid(), uid()
        result = await client.post(
            "/v1/sync/changes",
            headers=auth(key),
            json={"device": "phone", "conversations": [conv(first), conv(second)],
                  "messages": [msg(mid, first, text="original")]},
        )
        assert result.status_code == 200 and result.json()["accepted"] == 3

        for deleted in (False, True):
            result = await client.post(
                "/v1/sync/changes",
                headers=auth(key),
                json={"device": "phone", "messages": [msg(mid, second, deleted=deleted)]},
            )
            assert result.status_code == 200
            assert result.json()["accepted"] == 0
            assert result.json()["rejected"] == [{"mid": mid, "reason": "mid_conversation_mismatch"}]

        history = await client.get("/v1/sync/changes?since=0", headers=auth(key))
        saved = next(item for item in history.json()["messages"] if item["mid"] == mid)
        assert saved["cid"] == first and saved["text"] == "original" and not saved["deleted"]

        valid = await client.post(
            "/v1/sync/changes",
            headers=auth(key),
            json={"device": "phone", "messages": [msg(mid, first, deleted=True)]},
        )
        assert valid.status_code == 200 and valid.json()["accepted"] == 1
    finally:
        await client.aclose()
        await app.state.http.aclose()
        cloud.db.close()
