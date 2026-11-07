"""Exercise the exact boundary between Telegram topics and the persistent keyboard."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_channel_menu import manager, msg

from nanomuse.channels.menu import COPY, ChatMenu
from nanomuse.channels.telegram_topics import TelegramTopics


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["settings", "models", "chats", "new", "balance", "help"])
async def test_general_chat_keyboard_reaches_menu_without_selecting_topic(tmp_path, key):
    m = manager(tmp_path)
    m.channels["telegram"].topics_enabled = True
    m.menu = ChatMenu(m)
    m.menu.perform = AsyncMock()
    topics = TelegramTopics(SimpleNamespace(m=m))
    message = msg(text=COPY["ru"][key])
    assert not await topics.inbound(message)
    assert await m.menu.inbound(message)
    m.menu.perform.assert_awaited_once()


@pytest.mark.asyncio
async def test_ordinary_text_still_requires_topic_and_pending_rename_can_continue(tmp_path):
    m = manager(tmp_path)
    m.channels["telegram"].topics_enabled = True
    m.menu = ChatMenu(m)
    m.menu.show = AsyncMock()
    topics = TelegramTopics(SimpleNamespace(m=m))
    message = msg(text="Текст обычной задачи")
    assert await topics.inbound(message)
    m.menu.show.assert_awaited_once()
    m.menu.rename_pending[(message.channel, message.chat_id, message.sender_id)] = (
        9999999999,
        "cid",
    )
    assert not await topics.inbound(message)


@pytest.mark.asyncio
async def test_missing_catalogue_and_duplicate_topics_do_not_delete_history(tmp_path):
    from test_telegram_resume import world

    m, picker, topics = world(tmp_path)
    m.svc.sync.state = {"conversation_marks": {}}
    m.svc.sync.thread_of = lambda _: None
    m.svc.sync.catalog.rows = {}
    m.channels["telegram"]._call = AsyncMock()
    await topics._reconcile()
    assert len(topics.file.load()) == 1
    m.channels["telegram"]._call.assert_not_awaited()
    rows = dict(topics.file.load())
    duplicate = dict(next(iter(rows.values())), topic=11)
    rows[topics._key("account", "42", 11)] = duplicate
    topics.file.save(rows)
    m.svc.sync.catalog.rows = {"shared": {"archived": False}}
    await topics._reconcile()
    assert len(topics.file.load()) == 2
    m.channels["telegram"]._call.assert_not_awaited()


@pytest.mark.asyncio
async def test_restored_owner_uses_explicit_runtime_binding_only(tmp_path):
    from nanomuse.channels.manager import ChannelManager

    m = manager(tmp_path)
    m.svc.hub.signed_in = True
    settings = {"public": True, "cloud_per_chat": True}
    m.store.config = lambda _: SimpleNamespace(settings=settings, allow_from=[])
    m.store.resolved = lambda cfg: cfg.settings
    m.pairing.approved = lambda _: {
        "123456": {"chat_id": "123456", "runtime_account": m.svc.sync.account_id}
    }
    message = msg(chat="123456", sender="123456")
    assert await ChannelManager._cloud_key_for(m, "telegram", message) == ("", "")
    m._cloud_key_for = AsyncMock(return_value=("", ""))
    m.svc.settings.cloud = SimpleNamespace(base_url="https://cloud.example.test")
    m.svc.app.vault = SimpleNamespace(get=lambda _: "runtime-owner-key")
    menu = ChatMenu(m)
    assert (await menu._cloud_access(message))[0] == "runtime-owner-key"
    assert not menu.runtime_owner(msg(chat="654321", sender="654321"))
