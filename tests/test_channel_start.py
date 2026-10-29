from unittest.mock import AsyncMock

import pytest

from nanomuse.channels.base import InboundMessage
from nanomuse.channels.link_start import handle_start_link, start_token

TG = "TG" + "A" * 30
MX = "MX" + "B" * 30


def test_only_explicit_channel_bound_start_payloads_are_accepted():
    assert start_token("/start nl_" + TG, "telegram") == TG
    assert start_token("/start@sample_bot nl_" + MX, "max") == MX
    assert not start_token("/start nl_" + TG, "max")
    assert not start_token("/start nl_" + TG + "suffix", "telegram")
    assert not start_token(TG, "telegram")


class Link:
    redeem = AsyncMock(return_value="Connected")

    def configured(self):
        return True


class Channel:
    def __init__(self):
        self.link = Link()
        self.link.redeem = AsyncMock(return_value="Connected")
        self.send = AsyncMock()

    def _link(self):
        return self.link


async def test_start_link_is_resolved_without_a_model_or_manual_code():
    channel = Channel()
    msg = InboundMessage(
        channel="telegram", chat_id="chat", sender_id="sender", text="/start nl_" + TG
    )
    assert await handle_start_link(channel, msg, "ru")
    channel.link.redeem.assert_awaited_once_with("sender", TG, "", "ru")
    channel.send.assert_awaited_once_with("chat", "Connected")


@pytest.mark.parametrize("text", ["/start nl_" + TG, "/start nl_invalid"])
async def test_invalid_or_wrong_channel_link_never_reaches_a_model(text):
    channel = Channel()
    msg = InboundMessage(channel="max", chat_id="chat", sender_id="sender", text=text)
    assert await handle_start_link(channel, msg, "ru")
    channel.link.redeem.assert_not_awaited()


async def test_ordinary_start_is_left_to_the_existing_welcome():
    channel = Channel()
    msg = InboundMessage(channel="telegram", chat_id="chat", sender_id="sender", text="/start")
    assert not await handle_start_link(channel, msg, "ru")
    channel.send.assert_not_awaited()


async def test_only_a_successful_link_admits_the_sender_to_the_bot():
    from unittest.mock import Mock

    from nanomuse.channels.bot_copy import channel_text

    channel = Channel()
    callback = Mock()
    msg = InboundMessage(
        channel="telegram", chat_id="chat", sender_id="sender", text="/start nl_" + TG
    )
    channel.link.redeem.return_value = channel_text("ru", "link_wrong")
    await handle_start_link(channel, msg, "ru", callback)
    callback.assert_not_called()
    channel.link.redeem.return_value = channel_text("ru", "link_linked")
    await handle_start_link(channel, msg, "ru", callback)
    callback.assert_called_once_with()


def test_max_start_keeps_connection_payload_without_a_model():
    from unittest.mock import Mock

    from nanomuse.channels.max import MaxChannel

    channel = MaxChannel({})
    channel.bus = Mock()
    channel.handle_update(
        {
            "update_type": "bot_started",
            "chat_id": 19,
            "user": {"user_id": 17},
            "payload": "nl_" + MX,
        }
    )
    msg = channel.bus.inbound.call_args.args[0]
    assert msg.text == "/start nl_" + MX
    assert msg.sender_id == "17"


def test_max_borrows_the_relays_shared_settings_from_telegram():
    """The relay and its bot secret belong to the account, not to one messenger: a MAX QR
    must work even when the older config wrote them only under `[channels.telegram]`."""
    from types import SimpleNamespace

    from nanomuse.channels.max import MaxChannel

    class Store:
        def config(self, name):
            settings = (
                {"cloud_url": "https://relay.example.test", "cloud_bot_token": "shared-secret"}
                if name == "telegram"
                else {}
            )
            return SimpleNamespace(settings=settings)

        def resolved(self, cfg):
            return dict(cfg.settings)

    channel = MaxChannel({})
    channel.bus = SimpleNamespace(store=Store())
    link = channel._link()
    assert link.configured()
    assert link._base() == "https://relay.example.test"
    assert link._token() == "shared-secret"
