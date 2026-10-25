"""A paired messenger chooses an account chat; selections survive restarts."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from nanomuse.channels.store import _Watched
from nanomuse.channels.telegram_topics import TelegramTopics, split_chat
from nanomuse.cloud import CloudError
from nanomuse.logger import logger

COPY = {
    "en": {
        "history": "Recent messages",
        "empty_history": "There are no messages here yet.",
        "continue_here": "Write here to continue this chat.",
        "earlier": "Earlier messages",
        "later": "Later messages",
        "you": "You",
        "lika": "Lika",
        "chats": "Chats",
        "new": "New chat",
        "current": "Current chat",
        "choose": "Choose where to continue. The number shows unread messages here.",
        "empty": "No shared chats yet.",
        "unavailable": "Could not load your chats. Please try again.",
        "opened": "Chat selected",
        "old": "This chat is no longer available. Open the list again.",
        "more": "Next",
        "previous": "Previous",
        "back": "Back",
        "untitled": "New chat",
    },
    "zh": {
        "history": "最近消息",
        "empty_history": "此对话还没有消息。",
        "continue_here": "直接发送消息即可继续此对话。",
        "earlier": "更早消息",
        "later": "较新消息",
        "you": "你",
        "lika": "Lika",
        "chats": "对话",
        "new": "新对话",
        "current": "当前对话",
        "choose": "请选择要继续的对话，数字表示此处未读消息。",
        "empty": "暂无共享对话。",
        "unavailable": "无法读取对话，请重试。",
        "opened": "已选择对话",
        "old": "此对话已不可用，请重新打开列表。",
        "more": "下一页",
        "previous": "上一页",
        "back": "返回",
        "untitled": "新对话",
    },
    "ru": {
        "history": "Последние сообщения",
        "empty_history": "В этой беседе пока нет сообщений.",
        "continue_here": "Пишите здесь, чтобы продолжить эту беседу.",
        "earlier": "Ранее",
        "later": "Позже",
        "you": "Вы",
        "lika": "Лика",
        "chats": "Чаты",
        "new": "Новый чат",
        "current": "Сейчас открыт",
        "choose": "Выберите, где продолжить. Число рядом показывает непрочитанные сообщения здесь.",
        "empty": "Общих чатов пока нет.",
        "unavailable": "Не удалось загрузить ваши чаты. Попробуйте ещё раз.",
        "opened": "Выбран чат",
        "old": "Этот чат больше недоступен. Откройте список заново.",
        "more": "Дальше",
        "previous": "Назад по списку",
        "back": "В меню",
        "untitled": "Новый чат",
    },
}


class ChatPicker:
    def __init__(self, manager: Any):
        self.m = manager
        self.file = _Watched(Path(manager.svc.data_dir) / "channels" / "selected-chats.json")
        self.topics = TelegramTopics(self)
        self.pins: dict[str, set[tuple[str, str]]] = {}
        self.remote_threads: set[str] = set()
        self.deferred: dict[str, dict[str, dict]] = {}
        self.expected: dict[tuple[str, str, str, str, str], float] = {}

    @staticmethod
    def reader(channel: str, chat: str) -> str:
        return "messenger-" + hashlib.sha256((channel + "\n" + chat).encode()).hexdigest()[:32]

    def selected(self, channel: str, chat: str) -> str:
        value = self.file.load().get(self.reader(channel, chat), {})
        return (
            str(value.get("thread") or "")
            if isinstance(value, dict) and value.get("account") == self.m.svc.sync.account_id
            else ""
        )

    def bind(self, msg: Any, thread: str, *, fresh: bool = False) -> None:
        data = dict(self.file.load())
        data[self.reader(msg.channel, msg.chat_id)] = {
            "thread": thread,
            "account": self.m.svc.sync.account_id,
            "fresh_title": fresh,
        }
        self.file.save(data)
        if menu := getattr(self.m, "menu", None):
            menu.inherit_access(msg, thread)

    def title_first_request(self, thread: str, text: str) -> None:
        data = dict(self.file.load())
        entries = [
            entry
            for entry in data.values()
            if isinstance(entry, dict)
            and entry.get("thread") == thread
            and entry.get("account") == self.m.svc.sync.account_id
            and entry.get("fresh_title")
        ]
        if not entries:
            return
        current = self.m.svc.threads.get(thread)
        if current is not None and current.title in {c["untitled"] for c in COPY.values()}:
            title = " ".join(text.split())[:60]
            if title:
                self.m.svc.rename_thread(thread, title)
        for entry in entries:
            entry["fresh_title"] = False
        self.file.save(data)
        if self.topics.enabled() and hasattr(self.m, "_spawn"):
            self.m._spawn(self.topics.rename(thread))

    def active_threads(self) -> set[str]:
        account = self.m.svc.sync.account_id
        result = {
            str(v["thread"])
            for v in self.file.load().values()
            if isinstance(v, dict) and v.get("account") == account and v.get("thread")
        }
        result.update(self.topics.new_threads())
        from nanomuse.channels.manager import thread_id_for

        for channel in ("telegram", "max"):
            for sender, entry in self.m.pairing.approved(channel).items():
                chat = str(entry.get("chat_id") or sender)
                if not self.selected(channel, chat):
                    result.add(thread_id_for(channel, chat))
        return result

    def expect(self, thread: str, endpoint: tuple[str, str], role: str, text: str) -> None:
        now = time.monotonic()
        self.expected = {k: v for k, v in self.expected.items() if v > now}
        if len(self.expected) >= 2048:
            self.expected.pop(next(iter(self.expected)))
        digest = hashlib.sha256(text.encode()).hexdigest()
        self.expected[(thread, *endpoint, role, digest)] = now + 120

    def consume(self, thread: str, endpoint: tuple[str, str], role: str, text: str) -> bool:
        digest = hashlib.sha256(text.encode()).hexdigest()
        expires = self.expected.pop((thread, *endpoint, role, digest), 0)
        return expires > time.monotonic()

    def targets(self, thread: str) -> list[tuple[str, str]]:
        result = []
        for channel in ("telegram", "max"):
            if channel not in self.m.channels or not self.m.store.config(channel).enabled:
                continue
            for sender, entry in self.m.pairing.approved(channel).items():
                chat = str(entry.get("chat_id") or sender)
                if self.selected(channel, chat) == thread:
                    result.append((channel, chat))
        result.extend(self.topics.targets(thread))
        return list({self.topics.redirect(thread, channel, chat) for channel, chat in result})

    def reply_text(self, thread: str, endpoint: tuple[str, str], text: str) -> str:
        selected = self.selected(*endpoint)
        if selected and selected != thread:
            old = self.m.svc.threads.get(thread)
            return "📁 " + (old.title if old else thread) + "\n\n" + text
        return text

    @staticmethod
    async def _cloud_read(call: Callable[..., Awaitable[Any]], *args: Any) -> Any:
        # Retry idempotent reads only; never repeat creation, payment or model calls.
        for attempt in range(3):
            try:
                return await asyncio.wait_for(call(*args), timeout=8)
            except CloudError as exc:
                if (exc.status != 0 and not 500 <= exc.status < 600) or attempt == 2:
                    raise
            except (httpx.TransportError, TimeoutError):
                if attempt == 2:
                    raise
            await asyncio.sleep(0.3 * (attempt + 1))
        raise RuntimeError("unreachable")

    async def available(self, msg: Any) -> bool:
        # A shared runtime must not give one per-chat account another account's catalogue.
        resolver = getattr(self.m, "_cloud_key_for", None)
        if resolver:
            key, _ = await resolver(msg.channel, msg)
            if key == "!":
                return False
            if key:
                from nanomuse.cloud import CloudClient

                client = CloudClient(base_url=self.m.svc.settings.cloud.base_url, api_key=key)
                try:
                    me = await self._cloud_read(client.me)
                    if me.get("account", {}).get("id") != self.m.svc.sync.account_id:
                        return False
                finally:
                    await client.close()
        return bool(self.m.svc.sync.active)

    async def show(self, msg: Any, page: int = 0) -> None:
        c = COPY.get(self.m.menu.language(msg), COPY["ru"])
        try:
            if not await self.available(msg):
                raise ValueError("account unavailable")
            await self._cloud_read(self.m.svc.sync.pull)
            engine = self.m.svc.sync
            if not engine.catalog.supported:
                raise ValueError("catalog unavailable")
            reader = self.reader(msg.channel, msg.chat_id)
            listing = await self._cloud_read(engine.client.catalog, reader)
            entries = listing.get("conversations", [])
            current = self.selected(msg.channel, msg.chat_id)
            general_topic = self.topics.enabled(msg.channel) and not split_chat(msg.chat_id)[1]
            if general_topic:
                current = ""
            elif not current:
                current = self.m._thread_for(msg)
            cid = engine.cid_of(current)
            through = int(engine.state.get("message_cursors", {}).get(cid) or 0)
            if cid and through:
                await self._cloud_read(engine.client.read, cid, reader, through)
                listing = await self._cloud_read(engine.client.catalog, reader)
                entries = listing.get("conversations", [])
            title = self.m.svc.threads.get(current)
            text = (
                c["choose"]
                + "\n\n"
                + c["current"]
                + ": "
                + (title.title if title else c["untitled"])
            )
            if general_topic:
                text = c["choose"]
            rows = []
            page = max(0, min(page, max(0, (len(entries) - 1) // 8)))
            for row in entries[page * 8 : page * 8 + 8]:
                tid = engine.thread_of(row["cid"])
                mark = "✓ " if tid and tid.id == current else ""
                badge = " · " + str(row["unread"]) if row.get("unread") else ""
                label = mark + (row.get("title") or c["untitled"])[:44] + badge
                rows.append([(label, self.m.menu.ticket(msg, "chat:select:" + row["cid"]))])
            if not entries:
                text += "\n" + c["empty"]
            nav = []
            if page:
                nav.append((c["previous"], self.m.menu.ticket(msg, "chat:page:" + str(page - 1))))
            if (page + 1) * 8 < len(entries):
                nav.append((c["more"], self.m.menu.ticket(msg, "chat:page:" + str(page + 1))))
            if nav:
                rows.append(nav)
            rows.extend(
                [
                    [(c["new"], self.m.menu.ticket(msg, "chat:new"))],
                    [(c["back"], self.m.menu.ticket(msg, "home"))],
                ]
            )
            await self.m.menu.send(msg, text, rows)
            logger.info(
                "channel {}: shared chat list delivered ({} entries)", msg.channel, len(entries)
            )
        except Exception as exc:
            logger.warning(
                "channel {}: shared chat list failed ({})", msg.channel, type(exc).__name__
            )
            await self.m.menu.send(
                msg, c["unavailable"], [[(c["back"], self.m.menu.ticket(msg, "home"))]]
            )

    async def preview(self, msg: Any, thread: Any) -> None:
        c = COPY.get(self.m.menu.language(msg), COPY["ru"])
        events = [
            event
            for event in thread.timeline.events
            if event.get("type") in {"user", "assistant"} and str(event.get("text") or "").strip()
        ]
        events = events[-8:]
        text = c["opened"] + ": " + thread.title[:120]
        if events:
            text += "\n\n" + c["history"] + "\n"
            for event in events:
                role = c["you"] if event["type"] == "user" else c["lika"]
                body = str(event["text"]).strip()
                if len(body.encode("utf-16-le")) > 700:
                    body = body.encode("utf-16-le")[:700].decode("utf-16-le", errors="ignore") + "…"
                text += "\n" + role + ": " + body + "\n"
        else:
            text += "\n\n" + c["empty_history"]
        text += "\n" + c["continue_here"]
        rows = [[(c["chats"], self.m.menu.ticket(msg, "chats"))]]
        await self.m.menu.send(msg, text, rows)

    async def perform(self, msg: Any, action: str) -> None:
        c = COPY.get(self.m.menu.language(msg), COPY["ru"])
        if action == "chats" or action.startswith("chat:page:"):
            page = int(action.rsplit(":", 1)[-1]) if action.startswith("chat:page:") else 0
            await self.show(msg, page)
            return
        try:
            if not await self.available(msg):
                raise ValueError("account unavailable")
            engine = self.m.svc.sync
            await self._cloud_read(engine.pull)
            if not engine.catalog.supported:
                raise ValueError("catalog unavailable")
            if action.startswith("chat:open:"):
                from nanomuse.channels.conversation_open import open_conversation
                from nanomuse.channels.menu import COPY as MENU_COPY

                _, _, channel, cid = action.split(":", 3)
                result = await open_conversation(
                    self.m,
                    cid,
                    channel,
                    owner=msg.sender_id if channel == msg.channel else "",
                )
                await self.m.menu.send(
                    msg,
                    MENU_COPY[self.m.menu.language(msg)]["opened_channel"].format(
                        title=result["title"],
                        channel="Telegram" if channel == "telegram" else "MAX",
                    ),
                    [],
                )
                return
            if action.startswith("chat:history:"):
                history_cid, _page_text = action[len("chat:history:") :].rsplit(":", 1)
                selected = self.selected(msg.channel, msg.chat_id)
                if not selected or engine.cid_of(selected) != history_cid:
                    raise ValueError("selection changed")
                thread = engine.thread_of(engine.cid_of(selected)) if selected else None
                if thread is None or not engine._mine(thread):
                    raise ValueError("chat unavailable")
                await self.preview(msg, thread)
                return
            if action == "chat:new":
                thread = self.m.svc.create_thread(c["untitled"])
                engine.state["catalog_mode"] = True
                engine.thread_changed(thread)
                await engine.push()
            else:
                cid = action[len("chat:select:") :]
                if cid not in engine.catalog.rows:
                    raise ValueError("chat unavailable")
                thread = engine.thread_of(cid)
                if thread is None or not engine._mine(thread):
                    raise ValueError("chat unavailable")
            # Selecting does not overwrite the route or cancel work of the previous chat.
            await self._cloud_read(
                engine.catalog.open, thread.id, self.reader(msg.channel, msg.chat_id)
            )
            if await self.topics.open(msg, thread, fresh=action == "chat:new"):
                return
            self.bind(msg, thread.id, fresh=action == "chat:new")
            cid = engine.cid_of(thread.id)
            if cid:
                await self._cloud_read(
                    engine.client.read,
                    cid,
                    self.reader(msg.channel, msg.chat_id),
                    int(engine.state.get("message_cursors", {}).get(cid) or 0),
                )
            # Existing history is not replayed into a messenger as a burst of notifications.
            for event in thread.timeline.events:
                mid = str(event.get("mid") or "")
                role = str(event.get("type") or "")
                if mid and role in {"user", "assistant"}:
                    self.m.sync_receipts.mark(
                        self.m.sync_receipts.key(msg.channel, msg.chat_id, mid, role)
                    )
            await self.preview(msg, thread)
        except Exception:
            await self.m.menu.send(
                msg, c["old"], [[(c["chats"], self.m.menu.ticket(msg, "chats"))]]
            )
