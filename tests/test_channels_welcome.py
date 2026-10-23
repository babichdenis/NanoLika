import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nanomuse.channels.desktop_request import computer_request, select_computer
from nanomuse.channels.welcome import send_welcome_art


@pytest.mark.asyncio
async def test_artwork_does_not_call_provider_without_configuration(monkeypatch):
    monkeypatch.delenv("NANOLIKA_WELCOME_CONFIG", raising=False)
    channel = SimpleNamespace(name="telegram", settings={}, _call=AsyncMock())
    assert not await send_welcome_art(channel, "fixture-chat")
    channel._call.assert_not_called()


@pytest.mark.asyncio
async def test_telegram_artwork_uses_only_configured_brand_image(tmp_path, monkeypatch):
    p = tmp_path / "welcome.json"
    p.write_text(json.dumps({"telegram_photo": "fixture-file-id"}))
    monkeypatch.setenv("NANOLIKA_WELCOME_CONFIG", str(p))
    ch = SimpleNamespace(name="telegram", settings={}, _call=AsyncMock())
    assert await send_welcome_art(ch, "fixture-chat")
    method, body = ch._call.call_args.args
    assert method == "sendPhoto" and body["photo"] == "fixture-file-id"
    assert body["chat_id"] == "fixture-chat"


@pytest.mark.asyncio
async def test_max_artwork_has_image_attachment(tmp_path, monkeypatch):
    p = tmp_path / "welcome.json"
    p.write_text(json.dumps({"max_image_url": "https://example.invalid/welcome.png"}))
    monkeypatch.setenv("NANOLIKA_WELCOME_CONFIG", str(p))
    ch = SimpleNamespace(name="max", settings={}, _call=AsyncMock())
    assert await send_welcome_art(ch, "fixture-chat")
    kwargs = ch._call.call_args.kwargs
    assert kwargs["params"]["chat_id"] == "fixture-chat"
    assert kwargs["json"]["attachments"][0]["payload"]["url"].endswith("welcome.png")


def test_computer_routing_requires_explicit_command_and_unambiguous_device():
    assert computer_request("привет") is None
    assert computer_request("/computer") == ""
    assert computer_request("/computer@example reply only") == "reply only"
    computer = {"id": "other", "online": True, "kind": "computer", "actions": ["task"]}
    own = dict(computer, id="self")
    assert select_computer([own, computer], "self") == computer
    assert select_computer([dict(computer, online=False)], "self") is None
    assert select_computer([computer, dict(computer, id="second")], "self") is None
    assert select_computer([dict(computer, kind="phone")], "self") is None
    assert select_computer([dict(computer, actions=[])], "self") is None


@pytest.mark.asyncio
async def test_telegram_recovers_status_after_successful_empty_poll(monkeypatch):
    import asyncio

    from nanomuse.channels.telegram import TelegramChannel, TelegramError

    channel = TelegramChannel({"bot_token": "fixture-token"})
    status = []
    channel.bus = SimpleNamespace(set_status=lambda *args: status.append(args))
    channel._username = "fixture"
    channel._call = AsyncMock(
        side_effect=[TelegramError("fixture interruption"), [], asyncio.CancelledError()]
    )
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    with pytest.raises(asyncio.CancelledError):
        await channel._poll_loop()
    assert [row[1] for row in status] == ["connecting", "connected"]
