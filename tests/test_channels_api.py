"""``/api/channels`` as the app sees it: the shape of the view, secrets that never come
back, validation, the Feishu login endpoints, and that the rest of the API is untouched."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from nanomuse.channels import feishu
from nanomuse.config import Settings
from nanomuse.llm import MockLLM
from nanomuse.server import create_app
from nanomuse.server.service import MuseService


@pytest.fixture()
def server(settings: Settings) -> Iterator[tuple[TestClient, MuseService]]:
    settings.server.token = "secret-token"
    service = MuseService(settings, llm=MockLLM([]))
    app = create_app(settings, service)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer secret-token"
        yield client, service


def test_view_lists_supported_channels_off(server):
    client, service = server
    assert client.get("/api/channels", headers={"Authorization": ""}).status_code == 401
    view = client.get("/api/channels").json()
    assert [c["name"] for c in view["channels"]] == [
        "feishu",
        "dingtalk",
        "wecom",
        "telegram",
        "max",
    ]
    assert view["pending"] == []
    for item in view["channels"]:
        assert item["enabled"] is False and item["status"]["state"] == "off"
        assert (
            item["paired"] == [] and item["allow_from"] == [] and item["group_policy"] == "mention"
        )
        assert item["console_url"].startswith("https://")
        for field in item["fields"]:
            if field["kind"] == "secret":
                assert field["has_value"] is False and "value" not in field
            else:
                assert "value" in field
    telegram = view["channels"][3]
    assert telegram["sdk_available"] is True and telegram["install"] == ""
    assert telegram["fields"][0]["key"] == "bot_token" and telegram["fields"][0]["required"]
    feishu_view = view["channels"][0]
    assert feishu_view["install"] == "pip install 'nanomuse[feishu]'"
    assert [f["key"] for f in feishu_view["fields"]][:3] == ["app_id", "app_secret", "domain"]


def test_settings_are_saved_and_secrets_hidden(server, settings: Settings):
    client, service = server
    r = client.put(
        "/api/channels/telegram",
        json={
            "settings": {"bot_token": "123:abc", "proxy": " http://127.0.0.1:7890 "},
            "allow_from": ["42"],
        },
    )
    assert r.status_code == 200
    me = r.json()["channels"][3]
    fields = {f["key"]: f for f in me["fields"]}
    assert fields["bot_token"]["has_value"] is True and "value" not in fields["bot_token"]
    assert fields["proxy"]["value"] == "http://127.0.0.1:7890" and me["allow_from"] == ["42"]
    assert me["enabled"] is False and me["status"]["state"] == "off"
    text = (settings.data_dir / "channels" / "settings.json").read_text("utf-8")
    assert "123:abc" not in text and "{{vault:CHANNEL_TELEGRAM_BOT_TOKEN}}" in text
    # a missing required field keeps the channel "unconfigured" when switched on
    r = client.put("/api/channels/feishu", json={"enabled": True, "settings": {"app_id": "cli_1"}})
    me = r.json()["channels"][0]
    assert me["status"]["state"] in ("unconfigured", "missing_sdk")
    if me["status"]["state"] == "unconfigured":
        assert me["status"]["detail"] == "App Secret"
    # validation
    assert client.put("/api/channels/feishu", json={"group_policy": "all"}).status_code == 400
    assert (
        client.put("/api/channels/feishu", json={"settings": {"domain": "slack"}}).status_code
        == 400
    )
    assert client.put("/api/channels/slack", json={"enabled": True}).status_code == 404
    assert client.post("/api/channels/slack/test").status_code == 404
    assert client.delete("/api/channels/telegram/chats/nobody").status_code == 404
    # a channel that is off cannot be tested
    r = client.post("/api/channels/telegram/test")
    assert r.status_code == 409 and "switch it on" in r.json()["detail"]
    # the general settings and health endpoints still answer
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/settings").status_code == 200


def test_feishu_login_endpoints(server, monkeypatch: pytest.MonkeyPatch, settings: Settings):
    client, service = server
    assert client.post("/api/channels/telegram/login").status_code == 400
    polls = iter(
        [
            {"status": "pending", "domain": "feishu"},
            {
                "status": "succeeded",
                "app_id": "cli_new",
                "app_secret": "sec_new",
                "domain": "feishu",
            },
        ]
    )
    monkeypatch.setattr(
        feishu,
        "login_begin",
        lambda domain, name="": {
            "device_code": "dev-1",
            "url": f"https://accounts.feishu.cn/page/launcher?ticket=t&domain={domain}",
            "interval": 1,
            "expires_in": 300,
        },
    )
    monkeypatch.setattr(feishu, "login_poll", lambda code, domain="feishu": next(polls))
    r = client.post("/api/channels/feishu/login", json={"domain": "feishu"})
    assert r.status_code == 200
    body = r.json()
    assert body["device_code"] == "dev-1" and body["url"].startswith("https://accounts.feishu.cn/")
    assert body["qr_png"]  # base64 PNG for the app to show
    assert client.get("/api/channels").json()["channels"][0]["login"] is True
    assert client.get("/api/channels/feishu/login/other").status_code == 404
    assert client.get("/api/channels/feishu/login/dev-1").json()["status"] == "pending"
    r = client.get("/api/channels/feishu/login/dev-1")
    assert r.json() == {"status": "succeeded", "app_id": "cli_new"}
    me = client.get("/api/channels").json()["channels"][0]
    assert me["enabled"] is True and me["login"] is False
    fields = {f["key"]: f for f in me["fields"]}
    assert fields["app_id"]["value"] == "cli_new" and fields["app_secret"]["has_value"] is True
    assert "sec_new" not in (settings.data_dir / "channels" / "settings.json").read_text("utf-8")
    # without the SDK the state says what to install; with it, the connection is attempted
    assert me["status"]["state"] in ("missing_sdk", "connecting", "error", "connected")
    if me["status"]["state"] == "missing_sdk":
        assert me["status"]["detail"] == "pip install 'nanomuse[feishu]'"


def test_max_webhook_requires_an_actual_configured_max_channel(server):
    client, service = server
    from nanomuse.channels.base import Channel

    channels = service.channels.channels
    original = dict(channels)
    try:
        for wrong in (None, Channel({})):
            channels["max"] = wrong
            response = client.post("/api/channels/max/webhook", json=None)
            assert response.status_code == 503
            assert response.json()["detail"] == "MAX is not configured"
    finally:
        channels.clear()
        channels.update(original)
