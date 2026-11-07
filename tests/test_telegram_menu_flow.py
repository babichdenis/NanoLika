"""Telegram's button paths must work for ordinary visitors without running a model."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_channel_menu import manager, msg

from nanomuse.channels.manager import ChannelManager
from nanomuse.channels.menu import COPY, ChatMenu


@pytest.mark.asyncio
async def test_public_visitor_can_use_keyboard_without_pairing(tmp_path):
    m = manager(tmp_path)
    m.pairing.is_approved = lambda *_: False
    m.pairing.approved = lambda *_: {}
    m._allowed = lambda *_: False
    m.store.config = lambda _: SimpleNamespace(
        allow_from=[], settings={"public": True, "cloud_per_chat": True}
    )
    menu = ChatMenu(m)
    menu.perform = AsyncMock()
    for key, action in [
        ("chats", "chats"),
        ("new", "chat:new"),
        ("models", "models"),
        ("balance", "balance"),
        ("settings", "settings"),
        ("help", "help"),
    ]:
        message = msg(chat="123456", sender="123456", text=COPY["ru"][key])
        assert await menu.inbound(message)
        menu.perform.assert_awaited_with(message, action)
    assert not menu.private_owner(msg(chat="654321", sender="123456"))
    assert not menu.private_owner(msg(chat="123456", sender="123456", is_group=True))
    m.store.config = lambda _: SimpleNamespace(allow_from=[], settings={"public": True})
    assert not menu.private_owner(msg(chat="123456", sender="123456"))


@pytest.mark.asyncio
async def test_computer_navigation_and_account_webapp_work(tmp_path):
    m = manager(tmp_path)
    menu = ChatMenu(m)
    await menu.callback("telegram", "chat", "owner", {"menu": "nav:computer"})
    assert "download.html" in str(m.channels["telegram"]._call.call_args)
    await menu.perform(msg(), "settings")
    body = m.channels["telegram"]._call.call_args.args[1]
    buttons = [b for row in body["reply_markup"]["inline_keyboard"] for b in row]
    account = next(b for b in buttons if "web_app" in b)
    assert account["web_app"]["url"].endswith("/web/telegram.html?next=account")


@pytest.mark.asyncio
async def test_start_installs_keyboard_instead_of_three_links(monkeypatch):
    monkeypatch.setattr("nanomuse.channels.manager.send_welcome_art", AsyncMock())
    m = SimpleNamespace(
        lang="ru",
        channels={"telegram": SimpleNamespace()},
        menu=SimpleNamespace(home=AsyncMock()),
        _send=AsyncMock(),
        welcome_links=lambda _: pytest.fail("legacy start links"),
    )
    await ChannelManager._greet(m, "telegram", "123456")
    call = m.menu.home.await_args
    assert call.args[0].sender_id == "123456"
    assert "Лика" in call.args[1]
    m._send.assert_not_awaited()


@pytest.mark.asyncio
async def test_rename_is_scoped_and_canceled_when_chat_changes(tmp_path):
    m = manager(tmp_path)
    m.svc.sync.cid_of = lambda _: "cid-one"
    menu = ChatMenu(m)
    menu.chat_rename = AsyncMock(return_value="renamed")
    await menu.perform(msg(), "chat-rename")
    assert await menu.inbound(msg(text="Поездка"))
    menu.chat_rename.assert_awaited_once()
    await menu.perform(msg(), "chat-rename")
    m.svc.sync.cid_of = lambda _: "cid-two"
    assert await menu.inbound(msg(text="Не то название"))
    assert menu.chat_rename.await_count == 1


@pytest.mark.asyncio
async def test_computer_button_uses_existing_handler_and_back_cancels(tmp_path):
    menu = ChatMenu(manager(tmp_path))
    await menu.perform(msg(), "computer-task")
    task = msg(text="Проверь папку загрузок")
    assert not await menu.inbound(task)
    assert task.text == "/computer Проверь папку загрузок"
    await menu.perform(msg(), "computer-task")
    await menu.perform(msg(), "home")
    ordinary = msg(text="Привет")
    assert not await menu.inbound(ordinary)
    assert ordinary.text == "Привет"


@pytest.mark.asyncio
async def test_delete_requires_bound_confirmation(tmp_path):
    m = manager(tmp_path)
    m.svc.sync.cid_of = lambda _: "cid-one"
    menu = ChatMenu(m)
    menu.chat_delete = AsyncMock(return_value="deleted")
    await menu.perform(msg(), "chat-delete")
    menu.chat_delete.assert_not_awaited()
    token = next(k for k, v in menu.tickets.items() if v[2].startswith("chat-delete-confirm:"))
    await menu.callback("telegram", "chat", "intruder", {"menu": token})
    menu.chat_delete.assert_not_awaited()
    await menu.callback("telegram", "chat", "owner", {"menu": token})
    menu.chat_delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_group_start_cannot_approve_desktop_login():
    m = SimpleNamespace(channels={"telegram": object()}, _approve_desktop_login=AsyncMock())
    message = msg(chat="-123456", sender="123456", text="/start nl_LG" + "A" * 30, is_group=True)
    await ChannelManager._handle_inbound(m, message)
    m._approve_desktop_login.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_delete_button_cannot_delete_a_different_chat(tmp_path):
    m = manager(tmp_path)
    m.svc.sync.cid_of = lambda _: "first"
    menu = ChatMenu(m)
    menu.chat_delete = AsyncMock(return_value="deleted")
    await menu.perform(msg(), "chat-delete")
    token = next(k for k, v in menu.tickets.items() if v[2].startswith("chat-delete-confirm:"))
    m.svc.sync.cid_of = lambda _: "second"
    await menu.callback("telegram", "chat", "owner", {"menu": token})
    menu.chat_delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_account_connection_has_a_button(tmp_path):
    m = manager(tmp_path)
    m._send_connect_code = AsyncMock()
    menu = ChatMenu(m)
    await menu.perform(msg(), "help")
    token = next(k for k, v in menu.tickets.items() if v[2] == "connect")
    await menu.callback("telegram", "chat", "owner", {"menu": token})
    m._send_connect_code.assert_awaited_once()


@pytest.mark.asyncio
async def test_permission_confirmation_does_not_follow_a_changed_chat(tmp_path):
    m = manager(tmp_path)
    menu = ChatMenu(m)
    await menu.perform(msg(), "mode:full-access")
    token = next(k for k, v in menu.tickets.items() if v[2] == "mode-confirm:full-access")
    m._thread_for = lambda _: "a-different-thread"
    await menu.callback("telegram", "chat", "owner", {"menu": token})
    assert menu.preferences.mode("a-different-thread") == ""


@pytest.mark.asyncio
async def test_public_paired_telegram_uses_relay_identity_not_machine_account(
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
    key, _ = await ChannelManager._cloud_key_for(m, "telegram", msg(chat="123456", sender="123456"))
    assert key == "own-chat-key"
    assert client.post.await_args.kwargs["json"]["chat_id"] == "123456"


@pytest.mark.asyncio
async def test_other_public_account_never_uses_shared_catalog(tmp_path):
    m = manager(tmp_path)
    m.store.config = lambda _: SimpleNamespace(
        allow_from=[], settings={"public": True, "cloud_per_chat": True}
    )
    m.picker = SimpleNamespace(available=AsyncMock(return_value=False), perform=AsyncMock())
    menu = ChatMenu(m)
    message = msg(chat="123456", sender="123456", text="")
    await menu.perform(message, "chats")
    await menu.perform(message, "chat:new")
    m.picker.perform.assert_not_awaited()
    payload = m.channels["telegram"]._call.call_args.args[1]
    assert payload["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"].endswith(
        "/web/telegram.html"
    )


@pytest.mark.asyncio
async def test_computer_handoff_does_not_send_a_task_or_use_owner_hub(tmp_path):
    m = manager(tmp_path)
    menu = ChatMenu(m)
    message = msg(text="")
    await menu.cloud_handoff(message, computer=True)
    payload = m.channels["telegram"]._call.call_args.args[1]
    assert "Задача не отправлена" in payload["text"]
    assert payload["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"].endswith(
        "?next=account"
    )


@pytest.mark.asyncio
async def test_public_computer_request_cannot_reach_shared_owner_device(tmp_path):
    from unittest.mock import Mock

    m = manager(tmp_path)
    m._answer_to = {}
    m._pay_waiting = {}
    m.pairing.touch = Mock()
    m.picker = SimpleNamespace(
        available=AsyncMock(return_value=False),
        topics=SimpleNamespace(resume=lambda msg: msg, inbound=AsyncMock(return_value=False)),
    )
    m.menu = SimpleNamespace(
        inbound=AsyncMock(return_value=False),
        public_private=lambda _: True,
        native_account=AsyncMock(return_value=False),
        cloud_handoff=AsyncMock(),
    )
    m.svc.hub.others = lambda: pytest.fail("owner device lookup must not run")
    message = msg(text="/computer Проверь загрузки")
    await ChannelManager._handle_inbound(m, message)
    m.menu.cloud_handoff.assert_awaited_once_with(message, computer=True)


@pytest.mark.asyncio
async def test_other_public_account_cannot_enable_shared_runtime_full_access(tmp_path):
    m = manager(tmp_path)
    m.store.config = lambda _: SimpleNamespace(
        allow_from=[], settings={"public": True, "cloud_per_chat": True}
    )
    m.picker = SimpleNamespace(available=AsyncMock(return_value=False))
    menu = ChatMenu(m)
    message = msg(chat="123456", sender="123456")
    await menu.perform(message, "mode-confirm:full-access")
    assert menu.preferences.mode(m._thread_for(message)) == ""
    payload = m.channels["telegram"]._call.call_args.args[1]
    assert payload["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"].endswith(
        "?next=account"
    )
