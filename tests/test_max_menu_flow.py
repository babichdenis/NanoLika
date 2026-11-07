"""MAX uses signed private-dialog callbacks, with distinct sender and chat identities."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_channel_menu import manager, msg

from nanomuse.channels.manager import ChannelManager
from nanomuse.channels.menu import COPY, ChatMenu


def public_manager(tmp_path):
    m = manager(tmp_path)
    m.pairing.is_approved = lambda *_: False
    m.pairing.approved = lambda *_: {}
    m._allowed = lambda *_: False
    m.store.config = lambda _: SimpleNamespace(
        allow_from=[], settings={"public": True, "cloud_per_chat": True}
    )
    m.channels["max"].bot_id = 123456789
    return m


@pytest.mark.asyncio
async def test_six_buttons_use_callbacks_and_open_the_current_max_app(tmp_path):
    m = public_manager(tmp_path)
    menu = ChatMenu(m)
    message = msg("max", chat="987654", sender="123456", text="")
    await menu.show(message)
    body = m.channels["max"]._call.call_args.kwargs["json"]
    rows = body["attachments"][0]["payload"]["buttons"]
    assert [[b["text"] for b in row] for row in rows[:3]] == [
        [COPY["ru"][k] for k in group]
        for group in [("chats", "new"), ("models", "balance"), ("settings", "help")]
    ]
    menu.perform = AsyncMock()
    for row in rows[:3]:
        for button in row:
            import json

            assert button["type"] == "callback"
            assert await menu.callback("max", "987654", "123456", json.loads(button["payload"]))
    assert menu.perform.await_count == 6
    assert rows[3][0]["type"] == "open_app"
    assert rows[3][0]["contact_id"] == 123456789
    assert not menu.private_owner(msg("max", chat="987654", sender="123456", is_group=True))


@pytest.mark.asyncio
async def test_typed_labels_work_for_max_without_pairing(tmp_path):
    menu = ChatMenu(public_manager(tmp_path))
    menu.perform = AsyncMock()
    for key in ("chats", "new", "models", "balance", "settings", "help"):
        assert await menu.inbound(msg("max", chat="987654", sender="123456", text=COPY["ru"][key]))
    assert menu.perform.await_count == 6


@pytest.mark.asyncio
async def test_other_account_cannot_access_runtime_chats_or_permissions(tmp_path):
    m = public_manager(tmp_path)
    m.picker = SimpleNamespace(available=AsyncMock(return_value=False), perform=AsyncMock())
    menu = ChatMenu(m)
    message = msg("max", chat="987654", sender="123456", text="")
    for action in ("chats", "chat:new", "permissions", "mode:full-access", "retention"):
        await menu.perform(message, action)
        body = m.channels["max"]._call.call_args.kwargs["json"]
        button = body["attachments"][0]["payload"]["buttons"][0][0]
        assert button["type"] == "open_app"
        assert button["contact_id"] == 123456789
        if action not in ("chats", "chat:new"):
            assert button["payload"] == "account"
    m.picker.perform.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_keeps_actual_max_sender_instead_of_dialog_id(monkeypatch):
    monkeypatch.setattr("nanomuse.channels.manager.send_welcome_art", AsyncMock())
    m = SimpleNamespace(
        lang="ru", channels={"max": SimpleNamespace()}, menu=SimpleNamespace(home=AsyncMock()),
        _send=AsyncMock(), welcome_links=lambda _: pytest.fail("legacy start links"),
    )
    await ChannelManager._greet(m, "max", "987654", "123456")
    message = m.menu.home.await_args.args[0]
    assert message.chat_id == "987654"
    assert message.sender_id == "123456"
    m._send.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_paired_max_uses_relay_identity_not_machine_account(
    tmp_path, monkeypatch
):
    from unittest.mock import MagicMock

    import httpx

    m = manager(tmp_path)
    cfg = SimpleNamespace(
        settings={
            "public": True,
            "cloud_per_chat": True,
            "cloud_url": "https://cloud.example.test",
            "cloud_bot_token": "fixture",
        }
    )
    m.store.config = lambda _: cfg
    m.store.resolved = lambda _: cfg.settings
    m.svc.hub.signed_in = True
    m.pairing.approved = lambda _: {"123456": {"chat_id": "987654"}}
    m._cloud_keys = {}
    m._cloud_customer_scopes = {}
    client = SimpleNamespace(
        post=AsyncMock(
            return_value=httpx.Response(
                200,
                json={"api_key": "own-chat-key", "account": {"id": "own-account"}, "models": []},
            )
        )
    )
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr("nanomuse.channels.manager.httpx.AsyncClient", lambda **_: context)
    key, _ = await ChannelManager._cloud_key_for(m, "max", msg("max", chat="987654", sender="123456"))
    assert key == "own-chat-key"
    assert client.post.await_args.kwargs["json"]["chat_id"] == "123456"

