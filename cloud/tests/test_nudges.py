"""Relay 0.18: the nudges policy — when the apps may ask for a star on GitHub — served to every
client from the relay instead of being baked into each app, and set from the operator's page."""

from __future__ import annotations

import pytest
from test_accounts import auth, make
from test_cloud import sign_up

from nanomuse_cloud.nudges import DEFAULT_NUDGES, BadNudges, merge, validate

ADMIN = {"X-Admin-Token": "admin"}


async def test_defaults_are_served_publicly_with_an_hour_of_cache():
    app, client, sender, up, cloud, settings = make()
    r = await client.get("/v1/nudges")
    assert r.status_code == 200, r.text
    assert r.headers.get("cache-control") == "public, max-age=3600"
    assert r.json() == DEFAULT_NUDGES
    star = r.json()["star"]
    assert star["moments"]["tasks"] == [3, 10, 30] and star["moments"]["days_used"] == [7, 30]
    assert star["cooldown_days"] == 7 and star["max_asks"] == 4 and star["enabled"] is True
    # the admin view says nothing is stored yet
    r = await client.get("/v1/admin/nudges", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["stored"] is False and r.json()["updated_at"] is None
    assert r.json()["nudges"] == DEFAULT_NUDGES and r.json()["defaults"] == DEFAULT_NUDGES
    assert (await client.get("/v1/admin/nudges")).status_code == 401


async def test_put_round_trip_bumps_the_version_and_reaches_me():
    app, client, sender, up, cloud, settings = make()
    body = {
        "star": {
            "enabled": True,
            "url": "https://github.com/nano-muse/nanoMuse",
            "moments": {
                "signed_in": False,
                "tasks": "10, 3, 50,3",
                "new_look": True,
                "exhausted": True,
                "days_used": [30],
                "goal_done": False,
            },
            "cooldown_days": 14,
            "max_asks": 3,
        },
        "extra": "dropped",
    }
    r = await client.put("/v1/admin/nudges", headers=ADMIN, json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["stored"] is True and out["updated_at"]
    policy = out["nudges"]
    assert policy["version"] == 2  # the defaults are version 1
    assert "extra" not in policy
    assert policy["star"]["moments"]["tasks"] == [3, 10, 50]  # distinct, sorted, from the page's string
    assert policy["star"]["moments"]["days_used"] == [30]
    assert policy["star"]["moments"]["signed_in"] is False and policy["star"]["moments"]["goal_done"] is False
    assert policy["star"]["cooldown_days"] == 14 and policy["star"]["max_asks"] == 3

    # the public endpoint and /v1/me carry the same object
    assert (await client.get("/v1/nudges")).json() == policy
    me_key = (await sign_up(client, sender, "13800138000", "pixel"))["api_key"]
    me = (await client.get("/v1/me", headers=auth(me_key))).json()
    assert me["nudges"] == policy

    # another save bumps again; a reset serves the defaults
    r = await client.put("/v1/admin/nudges", headers=ADMIN, json={"star": {"enabled": False}})
    assert r.status_code == 200 and r.json()["nudges"]["version"] == 3
    assert r.json()["nudges"]["star"]["enabled"] is False
    assert r.json()["nudges"]["star"]["moments"]["tasks"] == [3, 10, 30]  # what was left out comes from the defaults
    r = await client.put("/v1/admin/nudges", headers=ADMIN, json={"reset": True})
    assert r.status_code == 200 and r.json()["stored"] is False and r.json()["nudges"] == DEFAULT_NUDGES
    events = (await client.get("/v1/admin/events?kind=nudges.changed", headers=ADMIN)).json()["events"]
    assert len(events) == 3 and events[0]["detail"] == "reset to the defaults"
    assert (await client.put("/v1/admin/nudges", json={"star": {}})).status_code == 401


async def test_validation_errors_are_400_with_a_plain_message():
    app, client, sender, up, cloud, settings = make()
    bad = [
        ({"star": {"enabled": "maybe"}}, "star.enabled"),
        ({"star": {"url": "ftp://x"}}, "url"),
        ({"star": {"url": "https://" + "a" * 200}}, "url"),
        ({"star": {"cooldown_days": 400}}, "cooldown_days"),
        ({"star": {"cooldown_days": -1}}, "cooldown_days"),
        ({"star": {"max_asks": 51}}, "max_asks"),
        ({"star": {"moments": {"tasks": [0, 3]}}}, "tasks"),
        ({"star": {"moments": {"tasks": [1.5]}}}, "tasks"),
        ({"star": {"moments": {"tasks": "three"}}}, "tasks"),
        ({"star": {"moments": {"days_used": {"a": 1}}}}, "days_used"),
        ({"star": {"moments": {"goal_done": 7}}}, "goal_done"),
        ({"star": "on"}, "star"),
        ({"star": {"moments": []}}, "moments"),
        ({"version": 0}, "version"),
    ]
    for body, word in bad:
        r = await client.put("/v1/admin/nudges", headers=ADMIN, json=body)
        assert r.status_code == 400, (body, r.text)
        assert r.json()["error"]["code"] == "bad_request" and word in r.json()["error"]["message"], body
    r = await client.put("/v1/admin/nudges", headers={**ADMIN, "Content-Type": "application/json"}, content=b"[1]")
    assert r.status_code == 400
    # nothing bad was stored
    assert (await client.get("/v1/nudges")).json() == DEFAULT_NUDGES


async def test_card_text_round_trips_trimmed_and_defaults_to_empty():
    """After relay 0.22 the operator may also set the sentence on the card: ``star.text``
    (English) and ``star.text_zh`` (简体中文), each at most 200 code points, empty by default."""
    app, client, sender, up, cloud, settings = make()
    # the defaults carry the two keys, empty, so an app knows to show its own sentence
    policy = (await client.get("/v1/nudges")).json()
    assert policy["star"]["text"] == "" and policy["star"]["text_zh"] == ""
    # a PUT with the two set comes back trimmed, on the public endpoint and on /v1/me
    body = {"star": {"text": "  A star on GitHub helps others find NanoLika. ", "text_zh": "\u3000在 GitHub 点个 star，让更多人找到它。\n"}}
    r = await client.put("/v1/admin/nudges", headers=ADMIN, json=body)
    assert r.status_code == 200, r.text
    star = r.json()["nudges"]["star"]
    assert star["text"] == "A star on GitHub helps others find NanoLika."
    assert star["text_zh"] == "在 GitHub 点个 star，让更多人找到它。"
    assert r.json()["nudges"]["version"] == 2
    assert (await client.get("/v1/nudges")).json()["star"]["text_zh"] == star["text_zh"]
    me_key = (await sign_up(client, sender, "13800138000", "pixel"))["api_key"]
    me = (await client.get("/v1/me", headers=auth(me_key))).json()
    assert me["nudges"]["star"]["text"] == star["text"]
    events = (await client.get("/v1/admin/events?kind=nudges.changed", headers=ADMIN)).json()["events"]
    assert len(events) == 1 and "card text/text_zh" in events[0]["detail"]
    # a client that sends the whole policy without the two keys (an older console) clears
    # them back to the default — like every other key that is left out
    r = await client.put("/v1/admin/nudges", headers=ADMIN, json={"star": {"enabled": True}})
    assert r.status_code == 200
    assert r.json()["nudges"]["star"]["text"] == "" and r.json()["nudges"]["star"]["text_zh"] == ""
    # one of the two alone is fine; null is empty
    r = await client.put("/v1/admin/nudges", headers=ADMIN, json={"star": {"text_zh": "点个 star", "text": None}})
    assert r.status_code == 200
    assert r.json()["nudges"]["star"]["text_zh"] == "点个 star" and r.json()["nudges"]["star"]["text"] == ""


async def test_card_text_limit_counts_code_points_and_rejects_non_strings():
    app, client, sender, up, cloud, settings = make()
    # 200 is accepted, 201 is not — in Latin letters and in CJK alike (code points, not bytes)
    for ok in ("a" * 200, "星" * 200, "é" * 200):
        r = await client.put("/v1/admin/nudges", headers=ADMIN, json={"star": {"text": ok, "text_zh": ok}})
        assert r.status_code == 200, (len(ok), r.text)
        assert len(r.json()["nudges"]["star"]["text"]) == 200
    # whitespace around does not count: trimming happens before the limit
    r = await client.put("/v1/admin/nudges", headers=ADMIN, json={"star": {"text": "  " + "a" * 200 + "  "}})
    assert r.status_code == 200, r.text
    for too_long, key in (("a" * 201, "text"), ("星" * 201, "text_zh"), ("星" * 100 + "a" * 101, "text")):
        r = await client.put("/v1/admin/nudges", headers=ADMIN, json={"star": {key: too_long}})
        assert r.status_code == 400, (key, r.text)
        assert r.json()["error"]["code"] == "bad_request"
        assert f"star.{key}" in r.json()["error"]["message"] and "200" in r.json()["error"]["message"]
    for not_a_string in (7, True, ["a"], {"en": "a"}, 1.5):
        for key in ("text", "text_zh"):
            r = await client.put("/v1/admin/nudges", headers=ADMIN, json={"star": {key: not_a_string}})
            assert r.status_code == 400, (key, not_a_string, r.text)
            assert f"star.{key}" in r.json()["error"]["message"]
    # the last good policy is what is served
    assert (await client.get("/v1/nudges")).json()["star"]["text"] == "a" * 200


def test_card_text_validate_directly_and_an_old_client_still_gets_a_whole_policy():
    assert validate({})["star"]["text"] == "" and validate({})["star"]["text_zh"] == ""
    assert validate({"star": {"text": " hi "}})["star"]["text"] == "hi"
    assert validate({"star": {"text_zh": "\u3000你好\u3000"}})["star"]["text_zh"] == "你好"
    with pytest.raises(BadNudges, match="star.text_zh"):
        validate({"star": {"text_zh": "x" * 201}})
    with pytest.raises(BadNudges, match="star.text "):
        validate({"star": {"text": 3}})
    # a row stored by relay 0.22 (no text keys) still parses and gains the empty defaults
    stored_0_22 = {
        "version": 4,
        "star": {"enabled": True, "url": "https://example.org/r", "moments": {"tasks": [5]}, "cooldown_days": 3, "max_asks": 2},
    }
    out = merge(stored_0_22)
    assert out["version"] == 4 and out["star"]["moments"]["tasks"] == [5]
    assert out["star"]["text"] == "" and out["star"]["text_zh"] == ""
    # every key a 0.22 app reads is still there, with the same types, when the text is set
    out = validate({"star": {"text": "Please star.", "text_zh": "点个 star。"}})
    for key in ("enabled", "url", "moments", "cooldown_days", "max_asks"):
        assert type(out["star"][key]) is type(DEFAULT_NUDGES["star"][key]), key
    assert set(out["star"]["moments"]) == set(DEFAULT_NUDGES["star"]["moments"])
    # a stored row whose text went bad falls back to the defaults, never a 500
    assert merge({"star": {"text": ["not", "a", "string"]}}) == DEFAULT_NUDGES


def test_validate_and_merge_directly():
    assert validate({}) == DEFAULT_NUDGES
    assert validate({"star": {"moments": {"tasks": []}}})["star"]["moments"]["tasks"] == []
    assert validate({"star": {"enabled": "off"}})["star"]["enabled"] is False
    assert validate({"star": {"max_asks": "0", "cooldown_days": 0}})["star"]["max_asks"] == 0
    with pytest.raises(BadNudges):
        validate([])
    with pytest.raises(BadNudges):
        validate({"star": {"moments": {"tasks": list(range(1, 30))}}})
    # a hand-edited row that no longer parses falls back to the defaults, never a 500
    assert merge("garbage") == DEFAULT_NUDGES
    assert merge(None) == DEFAULT_NUDGES
    assert merge({"star": {"enabled": False}})["star"]["enabled"] is False
    # the defaults themselves are never handed out by reference
    d = merge(None)
    d["star"]["moments"]["tasks"].append(99)
    assert DEFAULT_NUDGES["star"]["moments"]["tasks"] == [3, 10, 30]
