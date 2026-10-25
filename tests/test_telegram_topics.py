"""Native topic transport, ownership, persistence, history and reply isolation."""

import json
from datetime import UTC
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from test_channels_telegram import Bus

from nanomuse.channels.base import InboundMessage
from nanomuse.channels.chat_picker import ChatPicker
from nanomuse.channels.menu import ChatMenu
from nanomuse.channels.telegram import TelegramChannel
from nanomuse.channels.telegram_topics import physical_chat, split_chat, topic_chat


def make_manager(tmp_path):
    sync = SimpleNamespace(
        account_id="account",
        active=True,
        _mine=lambda t: True,
        thread_changed=lambda t: None,
        push=AsyncMock(),
    )
    m = SimpleNamespace(
        svc=SimpleNamespace(
            data_dir=tmp_path,
            sync=sync,
            app=SimpleNamespace(sentinel=SimpleNamespace()),
            threads={},
            settings=SimpleNamespace(channels=SimpleNamespace(shared_thread=False)),
            hub=SimpleNamespace(primary=lambda: None),
        ),
        channels={
            "telegram": SimpleNamespace(
                topics_enabled=True,
                _call=AsyncMock(return_value={"message_thread_id": 10}),
                send=AsyncMock(),
            )
        },
        pairing=SimpleNamespace(
            approved=lambda ch: {"100": {"chat_id": "100"}}, is_approved=lambda ch, s: s == "100"
        ),
        store=SimpleNamespace(config=lambda ch: SimpleNamespace(enabled=True, allow_from=[])),
        lang="ru",
        _allowed=lambda msg: msg.sender_id == "100",
        _send=AsyncMock(),
        routes={},
    )
    sync.cid_of = lambda tid: "cid-" + tid
    m._thread_for = lambda msg: m.picker.selected(msg.channel, msg.chat_id) or "legacy"
    m.sync_receipts = SimpleNamespace(mark=lambda key: None, key=lambda *a: a)
    m.picker = ChatPicker(m)
    m.menu = ChatMenu(m)
    m.picker.available = AsyncMock(return_value=True)

    def create(title):
        tid = "thread-" + str(len(m.svc.threads))
        thread = SimpleNamespace(id=tid, title=title, timeline=SimpleNamespace(events=[]))
        m.svc.threads[tid] = thread
        return thread

    def rename(tid, title):
        m.svc.threads[tid].title = title

    m.svc.create_thread = create
    m.svc.rename_thread = rename
    return m


def msg(topic=0, text="", title=""):
    return InboundMessage(
        channel="telegram",
        chat_id=topic_chat("100", topic),
        sender_id="100",
        text=text,
        chat_name=title,
    )


def test_destination_format_never_changes_max_and_has_no_ambiguous_prefixes():
    assert topic_chat("100", 12) == "100|12"
    assert split_chat("100|12") == ("100", 12)
    assert split_chat("100|0") == ("100|0", 0)
    assert physical_chat("max", "100|12") == "100|12"
    with pytest.raises(ValueError):
        topic_chat("not-a-chat", 12)


async def test_topic_transport_routes_messages_typing_files_and_callbacks(tmp_path):
    calls = []

    async def handle(req):
        method = req.url.path.rsplit("/", 1)[-1]
        if req.headers.get("content-type", "").startswith("application/json"):
            payload = json.loads(req.content)
            calls.append((method, payload))
        else:
            calls.append((method, req.content.decode(errors="ignore")))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 55}})

    channel = TelegramChannel({"bot_token": "test"}, transport=httpx.MockTransport(handle))
    channel._http = channel._make_http()
    channel.bus = Bus()
    try:
        await channel.send("100|12", "hello")
        await channel._call("sendChatAction", {"chat_id": "100|12", "action": "typing"})
        await channel.stream("100|12", "stream", "first", done=False)
        await channel.stream("100|12", "stream", "last", done=True)
        f = tmp_path / "test.txt"
        f.write_text("document")
        await channel._send_file("100|12", f)
        await channel._handle_callback(
            {
                "id": "callback",
                "from": {"id": 100},
                "data": "m:nav:chats",
                "message": {
                    "chat": {"id": 100, "type": "private"},
                    "message_id": 55,
                    "message_thread_id": 12,
                },
            }
        )
        assert channel.bus.actions[-1][1] == "100|12"
        assert channel.bus.actions[-1][3]["_menu_message_id"] == "55"
        for method, payload in calls:
            if isinstance(payload, dict) and "chat_id" in payload:
                assert payload["chat_id"] == "100"
                if method.startswith("send"):
                    assert payload["message_thread_id"] == 12
                else:
                    assert "message_thread_id" not in payload
        assert any(
            method == "sendDocument" and "message_thread_id" in data and "100|12" not in data
            for method, data in calls
            if isinstance(data, str)
        )
        # Topic menu edits own their buttons; approval cleanup cannot erase a new topic menu.
        assert not any(method == "editMessageReplyMarkup" for method, _ in calls)
    finally:
        await channel._http.aclose()


async def test_inbound_messages_and_creation_events_keep_topic_identity():
    c = TelegramChannel({})
    c.bus = Bus()
    await c._handle_update(
        {
            "message": {
                "message_id": 1,
                "message_thread_id": 12,
                "chat": {"id": 100, "type": "private"},
                "from": {"id": 100},
                "text": "hello",
            }
        }
    )
    await c._handle_update(
        {
            "message": {
                "message_id": 2,
                "message_thread_id": 13,
                "chat": {"id": 100, "type": "private"},
                "from": {"id": 100},
                "forum_topic_created": {"name": "Trip"},
            }
        }
    )
    assert [m.chat_id for m in c.bus.inbound_msgs] == ["100|12", "100|13"]
    assert c.bus.inbound_msgs[1].chat_name == "Trip" and c.bus.inbound_msgs[1].text == ""


async def test_open_reuses_topic_after_restart_without_replaying_history(tmp_path, monkeypatch):
    monkeypatch.setattr("nanomuse.channels.telegram_topics.asyncio.sleep", AsyncMock())
    m = make_manager(tmp_path)
    t = m.svc.create_thread("Trip")
    t.timeline.events = [
        {"id": str(i), "type": "user" if i % 2 == 0 else "assistant", "text": str(i)}
        for i in range(12)
    ] + [{"type": "tool", "text": "not shared"}]
    assert await m.picker.topics.open(msg(), t)
    assert m.picker.selected("telegram", "100|10") == t.id
    assert m.picker.topics.targets(t.id) == [("telegram", "100|10")]
    sent = m.channels["telegram"].send.call_args_list
    assert len(sent) == 9 and sent[0].args == ("100|10", "Вы:\n4")
    assert sent[-1].args == ("100|10", "Чат «Trip» продолжается.")
    assert all("not shared" not in c.args[1] for c in sent)
    m.picker = ChatPicker(m)
    m.picker.available = AsyncMock(return_value=True)
    assert await m.picker.topics.open(msg(), t)
    assert (
        len(
            [
                c
                for c in m.channels["telegram"]._call.call_args_list
                if c.args[0] == "createForumTopic"
            ]
        )
        == 1
    )
    assert m.channels["telegram"].send.await_count == 10
    assert m.picker.topics.file.path.stat().st_mode & 0o777 == 0o600


async def test_native_topics_create_distinct_account_chats_and_general_never_runs_model(tmp_path):
    m = make_manager(tmp_path)
    assert await m.picker.topics.inbound(msg(12, title="Home"))
    assert await m.picker.topics.inbound(msg(13, title="Work"))
    one = m.picker.selected("telegram", "100|12")
    two = m.picker.selected("telegram", "100|13")
    assert one and two and one != two
    assert not await m.picker.topics.inbound(msg(12, text="continue"))
    assert m._thread_for(msg(12)) == one and m._thread_for(msg(13)) == two
    m.menu.show = AsyncMock()
    assert await m.picker.topics.inbound(msg(text="hello"))
    m.menu.show.assert_awaited_once()
    assert not await m.picker.topics.inbound(msg(text="/chats"))
    m.picker.show = AsyncMock()
    await m.menu.perform(msg(), "permissions")
    m.picker.show.assert_awaited_once()


async def test_topic_ownership_account_change_and_unpairing_fail_closed(tmp_path):
    m = make_manager(tmp_path)
    t = m.svc.create_thread("Private")
    intruder = InboundMessage(channel="telegram", chat_id="100", sender_id="other")
    with pytest.raises(ValueError):
        await m.picker.topics.open(intruder, t)
    m.channels["telegram"]._call.assert_not_awaited()
    assert await m.picker.topics.open(msg(), t)
    assert m.picker.targets(t.id) == [("telegram", "100|10")]
    m.svc.sync.account_id = "other"
    assert not m.picker.topics.targets(t.id)
    m.svc.sync.account_id = "account"
    m.pairing.approved = lambda ch: {}
    assert not m.picker.topics.targets(t.id)


async def test_account_switch_during_native_topic_creation_never_binds_old_chat(tmp_path):
    m = make_manager(tmp_path)
    t = m.svc.create_thread("Private")

    async def switch(*args):
        m.svc.sync.account_id = "other"
        return {"message_thread_id": 10}

    m.channels["telegram"]._call = AsyncMock(side_effect=switch)
    with pytest.raises(ValueError):
        await m.picker.topics.open(msg(), t)
    assert not m.picker.topics.file.load()
    m.channels["telegram"].send.assert_not_awaited()


async def test_deleted_conversation_is_not_resurrected_by_writing_to_its_topic(tmp_path):
    m = make_manager(tmp_path)
    t = m.svc.create_thread("Deleted")
    assert await m.picker.topics.open(msg(), t)
    m.svc.threads.pop(t.id)
    assert await m.picker.topics.inbound(msg(10, text="hello"))
    assert not m.svc.threads


async def test_permission_callbacks_are_bound_to_their_own_topic(tmp_path):
    m = make_manager(tmp_path)
    t = m.svc.create_thread("One")
    assert await m.picker.topics.open(msg(), t)
    original = msg(10)
    ticket = m.menu.ticket(original, "mode-confirm:full-access")["menu"]
    await m.menu.callback("telegram", "100|11", "100", {"menu": ticket})
    assert m.menu.preferences.mode(t.id) == ""
    await m.menu.callback("telegram", "100|10", "100", {"menu": ticket})
    assert m.menu.preferences.mode(t.id) == "full-access"


async def test_synced_reply_goes_to_native_topic_and_not_general(tmp_path):
    from nanomuse.channels.manager import ChannelManager
    from nanomuse.channels.sync_delivery import SyncDeliveryReceipts
    from nanomuse.server.events import now_iso

    m = make_manager(tmp_path)
    t = m.svc.create_thread("Work")
    assert await m.picker.topics.open(msg(), t)
    m.channels["telegram"].send.reset_mock()
    m.sync_receipts = SyncDeliveryReceipts(tmp_path / "receipts.json")
    m.sync_receipts.enabled_at -= 1
    m.svc.hub.device_id = "bot"
    m.say = lambda en, zh, ru: ru
    event = {
        "type": "assistant",
        "thread": t.id,
        "mid": "remote-message",
        "via_device": "computer",
        "text": "Answer",
        "ts": now_iso(),
    }
    await ChannelManager._deliver_synced_channel(m, event)
    m.channels["telegram"].send.assert_awaited_once_with("100|10", "Answer")
    await ChannelManager._deliver_synced_channel(m, event)
    assert m.channels["telegram"].send.await_count == 1


async def test_live_reply_waits_until_recent_history_is_seeded(tmp_path, monkeypatch):
    from nanomuse.channels.manager import ChannelManager
    from nanomuse.channels.sync_delivery import SyncDeliveryReceipts
    from nanomuse.server.events import now_iso

    monkeypatch.setattr("nanomuse.channels.telegram_topics.asyncio.sleep", AsyncMock())
    m = make_manager(tmp_path)
    t = m.svc.create_thread("Work")
    t.timeline.events = [{"id": "old", "mid": "old-mid", "type": "user", "text": "Earlier"}]
    m.svc.hub.device_id = "bot"
    m.say = lambda en, zh, ru: ru
    m.sync_receipts = SyncDeliveryReceipts(tmp_path / "receipts.json")
    m.sync_receipts.enabled_at -= 1

    async def deliver(event):
        await ChannelManager._deliver_synced_channel(m, event)

    m._deliver_synced_channel = deliver

    async def on_send(chat, text):
        if text == "Вы:\nEarlier":
            await deliver(
                {
                    "type": "assistant",
                    "thread": t.id,
                    "mid": "new-mid",
                    "via_device": "computer",
                    "text": "New live reply",
                    "ts": now_iso(),
                }
            )

    m.channels["telegram"].send.side_effect = on_send
    assert await m.picker.topics.open(msg(), t)
    sent = [c.args[1] for c in m.channels["telegram"].send.call_args_list]
    assert sent == ["Вы:\nEarlier", "Чат «Work» продолжается.", "New live reply"]


async def test_waiting_topic_request_does_not_create_a_topic_after_account_switch(tmp_path):
    import asyncio

    m = make_manager(tmp_path)
    t = m.svc.create_thread("Private")
    await m.picker.topics._lock.acquire()
    task = asyncio.create_task(m.picker.topics.open(msg(), t))
    await asyncio.sleep(0)
    m.svc.sync.account_id = "other"
    m.picker.topics._lock.release()
    with pytest.raises(ValueError):
        await task
    m.channels["telegram"]._call.assert_not_awaited()


async def test_conversation_title_changes_rename_only_its_native_topic(tmp_path):
    m = make_manager(tmp_path)
    t = m.svc.create_thread("Original")
    assert await m.picker.topics.open(msg(), t)
    m.channels["telegram"]._call.reset_mock()
    t.title = "Trip plan"
    await m.picker.topics.rename(t.id)
    assert m.channels["telegram"]._call.call_args.args == (
        "editForumTopic",
        {"chat_id": "100", "message_thread_id": 10, "name": "Trip plan"},
    )
    assert next(iter(m.picker.topics.file.load().values()))["name"] == "Trip plan"
    m.channels["telegram"]._call.reset_mock()
    await m.picker.topics.rename(t.id)
    m.channels["telegram"]._call.assert_not_awaited()


async def test_opening_existing_topic_reopens_it_without_creating_another(tmp_path):
    m = make_manager(tmp_path)
    t = m.svc.create_thread("Archived topic")
    assert await m.picker.topics.open(msg(), t)
    m.channels["telegram"]._call.reset_mock()
    assert await m.picker.topics.open(msg(), t)
    assert m.channels["telegram"]._call.call_args_list[0].args[0] == "reopenForumTopic"
    assert not any(
        c.args[0] == "createForumTopic" for c in m.channels["telegram"]._call.call_args_list
    )


async def test_account_switch_during_first_topic_publish_does_not_continue_in_new_account(tmp_path):
    m = make_manager(tmp_path)

    async def switch():
        m.svc.sync.account_id = "other"

    m.svc.sync.push = AsyncMock(side_effect=switch)
    assert await m.picker.topics.inbound(msg(12, title="Private"))
    assert not m.picker.selected("telegram", "100|12")
    m.channels["telegram"].send.assert_not_awaited()


@pytest.mark.parametrize("channel", ["telegram", "max"])
def test_selected_chat_inherits_only_current_paired_owner_access(tmp_path, channel):
    from nanomuse.channels.manager import thread_id_for

    m = make_manager(tmp_path)
    message = InboundMessage(
        channel=channel,
        chat_id="100|10" if channel == "telegram" else "100",
        sender_id="100",
        text="hello",
    )
    legacy = thread_id_for(channel, "100")
    m.svc.threads[legacy] = SimpleNamespace(id=legacy)
    m.svc.threads["selected"] = SimpleNamespace(id="selected")
    m.svc.sync.owner_of = lambda tid: "account"
    m.menu.preferences.put(legacy, mode="full-access")
    m.picker.bind(message, "selected")
    assert m.menu.preferences.mode("selected") == "full-access"
    # A previous account or an unrelated sender must not inherit this choice.
    m.svc.sync.account_id = "other-account"
    m.svc.threads["other"] = SimpleNamespace(id="other")
    m.picker.bind(message, "other")
    assert m.menu.preferences.mode("other") == ""


def test_inherited_access_refreshes_but_explicit_restriction_survives(tmp_path):
    from dataclasses import replace

    from nanomuse.channels.manager import thread_id_for

    m = make_manager(tmp_path)
    message = InboundMessage(channel="telegram", chat_id="100|10", sender_id="100", text="hello")
    legacy = thread_id_for("telegram", "100")
    m.svc.sync.owner_of = lambda tid: "account"
    for tid in (legacy, "selected", "restricted", "intruder"):
        m.svc.threads[tid] = SimpleNamespace(id=tid)
    m.menu.preferences.put(legacy, mode="full-access")
    m.menu.preferences.put("restricted", mode="read-only")
    m.picker.bind(message, "selected")
    m.picker.bind(message, "restricted")
    m.picker.bind(replace(message, sender_id="stranger"), "intruder")
    assert m.menu.preferences.mode("restricted") == "read-only"
    assert m.menu.preferences.mode("intruder") == ""
    scope = m.menu.access_scope(message)
    m.menu.preferences.put(scope, mode="workspace-write")
    m.picker.bind(message, "selected")
    assert m.menu.preferences.mode("selected") == "workspace-write"
    m.menu.preferences.put("selected", mode="", access_source="")
    m.picker.bind(message, "selected")
    assert m.menu.preferences.mode("selected") == ""


@pytest.mark.asyncio
async def test_new_nonempty_synced_chat_gets_one_topic_without_general_menu(tmp_path):
    from datetime import datetime

    m = make_manager(tmp_path)
    thread = m.svc.create_thread("New work")
    thread.created_at = datetime.fromtimestamp(m.picker.topics._auto_since() + 1, UTC).isoformat()
    event = {"type": "user", "thread": thread.id, "text": "A new request", "mid": "first"}
    thread.timeline.events.append(event)
    m.menu.send = AsyncMock()
    await m.picker.topics.ensure_for_event(event)
    await m.picker.topics.ensure_for_event({**event, "type": "assistant", "text": "Ready"})
    creates = [
        c for c in m.channels["telegram"]._call.await_args_list if c.args[0] == "createForumTopic"
    ]
    assert len(creates) == 1
    assert creates[0].args[1]["icon_color"] in (0xFFD67E, 0x8EEE98, 0xFB6F5F)
    assert m.picker.selected("telegram", "100|10") == thread.id
    assert not m.picker.selected("telegram", "100")
    assert m.menu.send.await_count == 0  # no repeated instructions in General
    assert m.channels["telegram"].send.await_args_list[0].args[0] == "100|10"


@pytest.mark.asyncio
async def test_auto_topics_skip_old_empty_quiet_and_foreign_chats(tmp_path):
    from datetime import datetime

    m = make_manager(tmp_path)
    thread = m.svc.create_thread("Old work")
    thread.created_at = datetime.fromtimestamp(m.picker.topics._auto_since() - 10, UTC).isoformat()
    event = {"type": "user", "thread": thread.id, "text": "Old request"}
    await m.picker.topics.ensure_for_event(event)
    thread.created_at = datetime.fromtimestamp(m.picker.topics._auto_since() + 1, UTC).isoformat()
    await m.picker.topics.ensure_for_event({**event, "text": "  "})
    await m.picker.topics.ensure_for_event({**event, "quiet": True})
    m.svc.sync._mine = lambda _: False
    await m.picker.topics.ensure_for_event(event)
    m.svc.sync._mine = lambda _: True
    m.pairing.approved = lambda _: {"100": {"chat_id": "-100"}}
    await m.picker.topics.ensure_for_event(event)
    assert m.channels["telegram"]._call.await_count == 0


def test_auto_topic_cutoff_survives_restart_and_is_separate_per_account(tmp_path):
    from nanomuse.channels.telegram_topics import TelegramTopics

    m = make_manager(tmp_path)
    since = m.picker.topics._auto_since()
    restarted = TelegramTopics(m.picker)
    assert restarted._auto_since() == since
    m.svc.sync.account_id = "another-account"
    restarted._started = since + 20
    assert restarted._auto_since() == since + 20
    m.svc.sync.account_id = "account"
    assert restarted._auto_since() == since


@pytest.mark.asyncio
async def test_catalogue_discovers_quiet_first_page_without_selecting_old_chats(tmp_path):
    from datetime import datetime

    m = make_manager(tmp_path)
    new = m.svc.create_thread("New chat from computer")
    old = m.svc.create_thread("Existing chat")
    since = m.picker.topics._auto_since()
    new.created_at = datetime.fromtimestamp(since + 1, UTC).isoformat()
    old.created_at = datetime.fromtimestamp(since - 10, UTC).isoformat()
    assert new.id in m.picker.active_threads()
    assert old.id not in m.picker.active_threads()
    await m.picker.topics.discover()
    assert not m.channels["telegram"]._call.called  # no topic for an empty chat
    new.timeline.events.append({"type": "user", "text": "Hello", "mid": "first", "quiet": True})
    m.menu.send = AsyncMock()
    await m.picker.topics.discover()
    await m.picker.topics.discover()
    assert (
        len(
            [
                c
                for c in m.channels["telegram"]._call.await_args_list
                if c.args[0] == "createForumTopic"
            ]
        )
        == 1
    )
    assert not m.menu.send.called
