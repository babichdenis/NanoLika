"""Telegram topics: one place per conversation, named without the day the model wrote on it,
and never a topic (nor a conversation) for a command or a link code."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nanomuse.channels.base import InboundMessage
from nanomuse.channels.telegram_topics import TelegramTopics, normalize_name, service_text
from nanomuse.server.events import MAIN_THREAD

CHAT = "42"  # a private chat: its id is the owner's id
OWNER = "42"


def inbound(text: str, topic: int = 7, **kw: Any) -> InboundMessage:
    return InboundMessage(
        channel="telegram",
        chat_id=f"{CHAT}|{topic}" if topic else CHAT,
        sender_id=OWNER,
        sender_name="Денис",
        text=text,
        **kw,
    )


class World:
    """Just enough of the manager, the service and the picker for the topic half."""

    def __init__(self, data: Path) -> None:
        self.data = data
        self.created: list[str] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.sent: list[tuple[str, str]] = []
        self.bindings: dict[str, str] = {}
        self.next_topic = 40
        self.threads: dict[str, Any] = {}
        self.threads["main"] = self._thread("Main chat", "main")
        self.channel = SimpleNamespace(topics_enabled=True, _call=self._call, send=self._send)
        self.manager = SimpleNamespace(
            svc=SimpleNamespace(
                data_dir=data,
                threads=self.threads,
                sync=SimpleNamespace(
                    account_id="acc",
                    _mine=lambda thread: True,
                    cid_of=lambda thread_id: "",
                    thread_changed=lambda thread: None,
                    push=self._nothing,
                ),
                create_thread=self.create_thread,
                rename_thread=self.rename_thread,
            ),
            menu=SimpleNamespace(
                private_owner=lambda msg: True,
                language=lambda msg: "ru",
                show=self._menu,
                send=self._menu,
                inherit_access=lambda msg, thread: None,
            ),
            pairing=SimpleNamespace(approved=lambda channel: {OWNER: {"chat_id": OWNER}}),
            store=SimpleNamespace(
                config=lambda channel: SimpleNamespace(enabled=True, allow_from=[])
            ),
            channels={"telegram": self.channel},
            sync_receipts=SimpleNamespace(mark=lambda key: None, key=lambda *args: "k"),
            _deliver_synced_channel=self._nothing,
        )
        self.picker = SimpleNamespace(
            m=self.manager,
            bind=self.bind,
            selected=lambda channel, chat: self.bindings.get(f"{channel}:{chat}", ""),
            available=self._yes,
            targets=lambda thread: [],
            expect=lambda *args: None,
            reply_text=lambda thread, endpoint, text: text,
        )
        self.topics = TelegramTopics(self.picker)

    # ---- what the test says
    def thread(self, title: str) -> Any:
        """A conversation the app opened, as the picker's "new chat" does."""
        thread = self._thread(title)
        self.created.append(title)
        return thread

    def row(self, *, name: str, topic: int, thread: str) -> None:
        rows = dict(self.topics.file.load())
        rows[self.topics._key("acc", CHAT, topic)] = {
            "account": "acc",
            "chat": CHAT,
            "topic": topic,
            "owner": OWNER,
            "thread": thread,
            "cid": "",
            "name": name,
        }
        self.topics.file.save(rows)

    def topic_calls(self, method: str) -> list[dict[str, Any]]:
        return [params for called, params in self.calls if called == method]

    # ---- the rest of the fakes
    def _thread(self, title: str, thread_id: str | None = None) -> Any:
        thread_id = thread_id or f"t{len(self.threads)}"
        thread = SimpleNamespace(
            id=thread_id,
            title=title,
            created_at=datetime.now(UTC).isoformat(),
            timeline=SimpleNamespace(events=[]),
        )
        self.threads[thread_id] = thread
        return thread

    def create_thread(self, title: str) -> Any:
        return self.thread(title)

    def rename_thread(self, thread_id: str, title: str) -> None:
        self.threads[thread_id].title = title

    def bind(self, msg: Any, thread: str, *, fresh: bool = False) -> None:
        self.bindings[f"{msg.channel}:{msg.chat_id}"] = thread

    async def _call(self, method: str, params: dict[str, Any] | None = None, **kw: Any) -> Any:
        self.calls.append((method, dict(params or {})))
        if method == "createForumTopic":
            self.next_topic += 1
            return {"message_thread_id": self.next_topic}
        return {"message_id": 1}

    async def _send(self, chat_id: str, text: str, *, files: list[str] | None = None) -> str:
        self.sent.append((chat_id, text))
        return "m1"

    async def _menu(self, *args: Any, **kw: Any) -> None:
        return None

    async def _nothing(self, *args: Any, **kw: Any) -> None:
        return None

    async def _yes(self, *args: Any, **kw: Any) -> bool:
        return True


@pytest.fixture()
def world(tmp_path: Path) -> World:
    return World(tmp_path)


# ---------------------------------------------------------------------- names


def test_the_date_and_the_weekday_are_cut_from_a_name():
    assert normalize_name("Feed · воскресенье, 25 октября 2026 г.") == "Лента"
    assert normalize_name("Feed — 25.10.2026") == "Лента"
    assert normalize_name("Feed (25 октября)") == "Лента"
    assert normalize_name("Feed · sunday, 25 october 2026") == "Лента"
    assert normalize_name("Отчёт · 25.10.2026") == "Отчёт"


def test_a_name_keeps_its_words():
    assert normalize_name("Список  покупок    на неделю") == "Список покупок на неделю"
    # a weekday inside a phrase is not a date tail
    assert normalize_name("Встреча в пятницу") == "Встреча в пятницу"


def test_a_service_line_is_told_from_a_name():
    for line in (
        "/model",
        "/chats",
        "/menu",
        "V8WHK8",
        "Новый чат",
        "Открой этот чат на компьютере.",
    ):
        assert service_text(line) is True, line
    for line in ("Feed", "Купи хлеб", "Отчёт за неделю"):
        assert service_text(line) is False, line
    assert service_text("") is True


# ---------------------------------------------------------------------- one Feed topic


async def test_a_dated_feed_title_and_a_plain_one_are_one_topic(world: World):
    first = world.thread("Feed · воскресенье, 25 октября 2026 г.")
    assert await world.topics.open(inbound("Собери новости"), first) is True
    assert [call["name"] for call in world.topic_calls("createForumTopic")] == ["Лента"]
    assert len(world.topics.file.load()) == 1

    second = world.thread("Feed")
    assert await world.topics.open(inbound("Собери новости"), second) is True
    # the topic of yesterday is the topic of today: nothing new is created
    assert len(world.topic_calls("createForumTopic")) == 1
    rows = world.topics.file.load()
    assert len(rows) == 1
    saved = next(iter(rows.values()))
    assert saved["name"] == "Лента" and saved["thread"] == second.id


async def test_an_old_dated_topic_is_renamed_to_the_plain_name(world: World):
    world.row(name="Feed · воскресенье, 25 октября 2026 г.", topic=7, thread="old")
    thread = world.thread("Feed · понедельник, 26 октября 2026 г.")
    assert await world.topics.open(inbound("новости"), thread) is True
    assert world.topic_calls("createForumTopic") == []
    assert [edit["name"] for edit in world.topic_calls("editForumTopic")] == ["Лента"]
    rows = world.topics.file.load()
    saved = next(iter(rows.values()))
    assert len(rows) == 1 and saved["name"] == "Лента" and saved["thread"] == thread.id


async def test_a_rename_in_the_app_puts_the_plain_name_into_the_topic(world: World):
    thread = world.thread("Старое имя")
    world.row(name="Старое имя", topic=7, thread=thread.id)
    thread.title = "Feed · вторник, 27 октября 2026 г."
    await world.topics.rename(thread.id)
    assert [edit["name"] for edit in world.topic_calls("editForumTopic")] == ["Лента"]


# ---------------------------------------------------------------------- service lines


async def test_a_command_opens_neither_a_conversation_nor_a_topic(world: World):
    before = dict(world.topics.file.load())
    assert await world.topics.inbound(inbound("/model")) is False
    assert world.created == []
    assert world.topics.file.load() == before
    assert world.topic_calls("createForumTopic") == []
    # answered in the conversation this chat already continues: the main one
    assert world.bindings == {f"telegram:{CHAT}|7": MAIN_THREAD}


async def test_a_link_code_opens_neither_a_conversation_nor_a_topic(world: World):
    before = dict(world.topics.file.load())
    assert await world.topics.inbound(inbound("V8WHK8")) is False
    assert world.created == []
    assert world.topics.file.load() == before
    assert world.topic_calls("createForumTopic") == []


async def test_a_new_chat_is_one_conversation_and_one_topic(world: World):
    # what the "Новый чат" button does: a fresh conversation, then its topic
    thread = world.thread("Новый чат")
    assert await world.topics.open(inbound("Новый чат"), thread, fresh=True) is True
    assert world.created == ["Новый чат"]
    assert len(world.topic_calls("createForumTopic")) == 1
    rows = world.topics.file.load()
    assert len(rows) == 1 and next(iter(rows.values()))["name"] == "Новый чат"
    # the same conversation is never given a second topic
    assert await world.topics.open(inbound("Новый чат"), thread, fresh=True) is True
    assert len(world.topic_calls("createForumTopic")) == 1
    assert len(world.created) == 1


async def test_a_message_in_a_new_topic_names_the_conversation_without_the_date(world: World):
    assert await world.topics.inbound(inbound("Купи хлеб. Ещё молоко.")) is False
    assert world.created == ["Купи хлеб"]
    rows = world.topics.file.load()
    assert len(rows) == 1 and next(iter(rows.values()))["name"] == "Купи хлеб"
    assert world.topic_calls("createForumTopic") == []


# ---------------------------------------------------------------------- automatic topics


async def test_discover_makes_one_topic_for_one_conversation(world: World):
    thread = world.thread("Feed · воскресенье, 25 октября 2026 г.")
    thread.timeline.events.append({"type": "user", "text": "Собери новости", "mid": "m1"})
    await world.topics.discover()
    assert [call["name"] for call in world.topic_calls("createForumTopic")] == ["Лента"]
    await world.topics.discover()
    assert len(world.topic_calls("createForumTopic")) == 1
    assert len(world.topics.file.load()) == 1


async def test_a_conversation_the_bot_began_gets_no_topic(world: World):
    thread = world.thread("Утренняя сводка")
    thread.timeline.events.append({"type": "assistant", "text": "Доброе утро", "mid": "m1"})
    await world.topics.discover()
    assert world.topic_calls("createForumTopic") == []
    assert world.topics.file.load() == {}


async def test_catalogue_creates_topics_without_transcript_and_reconciles_archive(world: World):
    thread = world.thread("Shared chat")
    sync = world.manager.svc.sync
    sync.cid_of = lambda tid: "shared-cid" if tid == thread.id else ""
    sync.catalog = SimpleNamespace(supported=True, rows={"shared-cid": {"archived": False}})
    assert thread.timeline.events == []
    await world.topics.discover()
    assert len(world.topic_calls("createForumTopic")) == 1
    await world.topics.discover()
    assert len(world.topic_calls("createForumTopic")) == 1
    sync.catalog.rows["shared-cid"]["archived"] = True
    await world.topics.discover()
    assert len([call for call in world.topic_calls("editForumTopic") if call["name"].startswith("Архив")]) == 1
    await world.topics.discover()
    assert len([call for call in world.topic_calls("editForumTopic") if call["name"].startswith("Архив")]) == 1
    sync.catalog.rows["shared-cid"]["archived"] = False
    await world.topics.discover()
    assert world.topic_calls("editForumTopic")[-1]["name"] == "Shared chat"


async def test_equal_titles_do_not_merge_different_conversations(world: World):
    first = world.thread("Same title")
    second = world.thread("Same title")
    await world.topics.open(inbound("first"), first)
    await world.topics.open(inbound("second"), second)
    assert len(world.topic_calls("createForumTopic")) == 2
