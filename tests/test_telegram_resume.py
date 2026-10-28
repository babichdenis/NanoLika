"""Telegram's last explicit topic survives restarts without collecting chats in the root."""

from types import SimpleNamespace

from nanomuse.channels.base import InboundMessage
from nanomuse.channels.chat_picker import ChatPicker
from nanomuse.channels.store import _Watched
from nanomuse.channels.telegram_topics import TelegramTopics


def world(tmp_path):
    sync = SimpleNamespace(
        account_id="account",
        cid_of=lambda _: "shared",
        catalog=SimpleNamespace(supported=True, rows={"shared": {"archived": False}}),
    )
    manager = SimpleNamespace(
        svc=SimpleNamespace(data_dir=tmp_path, sync=sync),
        menu=SimpleNamespace(
            private_owner=lambda msg: msg.sender_id == "42", inherit_access=lambda *a: None
        ),
        channels={"telegram": SimpleNamespace(topics_enabled=True)},
        pairing=SimpleNamespace(approved=lambda _: {"42": {"chat_id": "42"}}),
        store=SimpleNamespace(config=lambda _: SimpleNamespace(enabled=True, allow_from=[])),
    )
    picker = ChatPicker.__new__(ChatPicker)
    picker.m = manager
    picker.file = _Watched(tmp_path / "channels/selected-chats.json")
    picker.topics = TelegramTopics(picker)
    topics = picker.topics
    topics.file.save(
        {
            topics._key("account", "42", 10): {
                "account": "account",
                "owner": "42",
                "chat": "42",
                "topic": 10,
                "thread": "main",
                "cid": "shared",
                "archived": False,
            }
        }
    )
    return manager, picker, topics


def message(chat="42|10", sender="42", text="continue"):
    return InboundMessage(channel="telegram", chat_id=chat, sender_id=sender, text=text)


def test_last_topic_survives_a_bot_restart(tmp_path):
    _, picker, topics = world(tmp_path)
    topics.remember(message())
    restarted = TelegramTopics(picker)
    assert restarted.resume(message("42")).chat_id == "42|10"
    assert restarted.resume(message("42", text="/chats")).chat_id == "42"


def test_other_accounts_owners_and_archives_do_not_resume(tmp_path):
    manager, _, topics = world(tmp_path)
    topics.remember(message())
    assert topics.resume(message("42", sender="99")).chat_id == "42"
    manager.svc.sync.catalog.rows["shared"]["archived"] = True
    assert topics.resume(message("42")).chat_id == "42"
    assert topics.targets("main") == []
    manager.svc.sync.account_id = "other-account"
    assert topics.resume(message("42")).chat_id == "42"


def test_shared_delivery_uses_only_native_topic_even_with_stale_root_selection(tmp_path):
    manager, picker, topics = world(tmp_path)
    picker.bind(message("42"), "main")
    assert picker.targets("main") == [("telegram", "42|10")]
    manager.svc.sync.catalog.rows["shared"]["archived"] = True
    assert picker.targets("main") == []
