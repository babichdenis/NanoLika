"""Native Telegram topics mapped to account conversations, never to another owner's chat."""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from nanomuse.channels.base import InboundMessage
from nanomuse.channels.store import _Watched
from nanomuse.logger import logger

_ENDPOINT = re.compile(r"^(-?\d+)\|([1-9]\d*)$")


def split_chat(chat: str) -> tuple[str, int]:
    match = _ENDPOINT.fullmatch(str(chat))
    return (match[1], int(match[2])) if match else (str(chat), 0)


def physical_chat(channel: str, chat: str) -> str:
    return split_chat(chat)[0] if channel == "telegram" else chat


def topic_chat(chat: str, topic: Any) -> str:
    physical = split_chat(chat)[0]
    value = int(topic or 0)
    if value < 0 or not re.fullmatch(r"-?\d+", physical):
        raise ValueError("invalid Telegram destination")
    return f"{physical}|{value}" if value else physical


COPY = {
    "en": {
        "continued": "Chat “{title}” continues.",
        "open": "Open the topic “{title}” in Telegram to continue.",
        "choose": "Choose a chat or create a topic in Telegram to continue.",
        "new": "New chat",
        "failed": "Could not open the topic. Open the chat list and try again.",
    },
    "zh": {
        "continued": "继续对话「{title}」。",
        "open": "请在 Telegram 中打开「{title}」话题继续。",
        "choose": "请选择对话或在 Telegram 中新建话题。",
        "new": "新对话",
        "failed": "无法打开话题，请重新打开对话列表后重试。",
    },
    "ru": {
        "continued": "Чат «{title}» продолжается.",
        "open": "Откройте тему «{title}» в Telegram, чтобы продолжить.",
        "choose": "Выберите беседу или создайте тему в Telegram, чтобы продолжить.",
        "new": "Новый чат",
        "failed": "Не удалось открыть тему. Откройте список бесед и попробуйте снова.",
    },
}


class TelegramTopics:
    def __init__(self, picker: Any):
        self.p = picker
        self.m = picker.m
        self.file = _Watched(Path(self.m.svc.data_dir) / "channels" / "telegram-topics.json")
        self.auto_file = _Watched(
            Path(self.m.svc.data_dir) / "channels" / "telegram-auto-topics.json"
        )
        self._started = time.time()
        self._auto_since()
        self._lock = asyncio.Lock()
        self._backfill: dict[tuple[str, str], dict[str, dict]] = {}

    def _auto_since(self) -> float:
        account = getattr(getattr(self.m.svc, "sync", None), "account_id", "")
        if not account:
            return self._started
        data = dict(self.auto_file.load())
        if account not in data:
            data[account] = {"since": self._started}
            self.auto_file.save(data)
        return float(data[account]["since"])

    def new_threads(self) -> set[str]:
        """New account chats need an initial history pull before they have a topic."""
        if not self.enabled() or not self.m.store.config("telegram").enabled:
            return set()
        if not any(
            str(p.get("chat_id") or owner) == owner
            for owner, p in self.m.pairing.approved("telegram").items()
        ):
            return set()
        sync = self.m.svc.sync
        if not sync.account_id:
            return set()
        since = self._auto_since()
        result = set()
        for thread in self.m.svc.threads.values():
            if not sync._mine(thread):
                continue
            try:
                created = datetime.fromisoformat(
                    str(thread.created_at).replace("Z", "+00:00")
                ).timestamp()
            except (AttributeError, TypeError, ValueError):
                continue
            if created >= since:
                result.add(thread.id)
        return result

    async def discover(self) -> None:
        """Initial catalogue pulls are quiet; only new chats may seed a native topic."""
        for tid in self.new_threads():
            thread = self.m.svc.threads.get(tid)
            if thread is None:
                continue
            event = next(
                (
                    e
                    for e in thread.timeline.events
                    if e.get("type") in {"user", "assistant"} and str(e.get("text") or "").strip()
                ),
                None,
            )
            if event is not None:
                await self.ensure_for_event({**event, "thread": tid, "quiet": False})

    async def ensure_for_event(self, event: dict[str, Any]) -> None:
        """Create topics only for newly created, nonempty account conversations."""
        if not self.enabled() or not self.m.store.config("telegram").enabled:
            return
        if not str(event.get("text") or "").strip() or event.get("quiet"):
            return
        if event.get("type") not in {"user", "assistant"}:
            return
        sync = self.m.svc.sync
        account = sync.account_id
        thread = self.m.svc.threads.get(str(event.get("thread") or ""))
        if not account or thread is None or not sync._mine(thread):
            return
        try:
            created = datetime.fromisoformat(
                str(thread.created_at).replace("Z", "+00:00")
            ).timestamp()
        except (AttributeError, TypeError, ValueError):
            return  # unknown age never causes a historical chat to be published
        if created < self._auto_since():
            return
        for owner, pair in self.m.pairing.approved("telegram").items():
            chat = str(pair.get("chat_id") or owner)
            if chat != owner or account != sync.account_id:
                continue
            existing = next(
                (
                    row
                    for row in self.file.load().values()
                    if isinstance(row, dict)
                    and row.get("thread") == thread.id
                    and row.get("chat") == chat
                    and self._valid(row)
                ),
                None,
            )
            if existing and existing.get("seeded"):
                continue
            msg = InboundMessage(channel="telegram", chat_id=chat, sender_id=owner)
            if not self.m.menu.private_owner(msg):
                continue
            try:
                await self.open(msg, thread, announce=False)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("telegram: automatic topic creation failed ({})", type(exc).__name__)

    def enabled(self, channel: str = "telegram") -> bool:
        return (
            channel == "telegram"
            and getattr(self.m.channels.get(channel), "topics_enabled", False) is True
        )

    def copy(self, msg: Any) -> dict[str, str]:
        return COPY.get(self.m.menu.language(msg), COPY["ru"])

    def _key(self, account: str, chat: str, topic: int) -> str:
        return hashlib.sha256(f"{account}\n{chat}\n{topic}".encode()).hexdigest()

    def _valid(self, row: dict) -> bool:
        if row.get("account") != self.m.svc.sync.account_id:
            return False
        owner = str(row.get("owner") or "")
        approved = self.m.pairing.approved("telegram").get(owner)
        if isinstance(approved, dict):
            return str(approved.get("chat_id") or owner) == row.get("chat")
        return owner in self.m.store.config("telegram").allow_from and owner == row.get("chat")

    def targets(self, thread: str) -> list[tuple[str, str]]:
        if not self.enabled() or not self.m.store.config("telegram").enabled:
            return []
        return [
            ("telegram", topic_chat(row["chat"], row["topic"]))
            for row in self.file.load().values()
            if isinstance(row, dict) and row.get("thread") == thread and self._valid(row)
        ]

    def redirect(self, thread: str, channel: str, chat: str) -> tuple[str, str]:
        if channel == "telegram":
            for endpoint in self.targets(thread):
                if split_chat(endpoint[1])[0] == split_chat(chat)[0]:
                    return endpoint
        return channel, chat

    def _put(self, msg: Any, topic: int, thread: Any, **extra: Any) -> None:
        chat = split_chat(msg.chat_id)[0]
        account = self.m.svc.sync.account_id
        rows = dict(self.file.load())
        key = self._key(account, chat, topic)
        old = rows.get(key, {})
        rows[key] = {
            **old,
            "account": account,
            "chat": chat,
            "topic": topic,
            "owner": msg.sender_id,
            "thread": thread.id,
            "name": old.get("name", thread.title),
            "cid": self.m.svc.sync.cid_of(thread.id),
            **extra,
        }
        self.file.save(rows)
        self.p.bind(replace(msg, chat_id=topic_chat(chat, topic)), thread.id)

    async def inbound(self, msg: Any) -> bool:
        """Return True when a service event/general-chat message needs no model turn."""
        if not self.enabled(msg.channel) or msg.is_group:
            return False
        chat, topic = split_chat(msg.chat_id)
        if not topic:
            command = msg.text.strip().lower().split("@")[0].split()[0:1]
            if command in [
                [name]
                for name in (
                    "/start",
                    "/help",
                    "/status",
                    "/menu",
                    "/chats",
                    "/chat",
                    "/newchat",
                    "/new_chat",
                    "/newsession",
                    "/new_session",
                    "/new",
                    "/balance",
                    "/language",
                    "/pay",
                    "/permissions",
                )
            ]:
                return False
            await self.m.menu.show(msg, note=self.copy(msg)["choose"])
            return True
        account = self.m.svc.sync.account_id
        if (
            not self.m.menu.private_owner(msg)
            or not await self.p.available(msg)
            or account != self.m.svc.sync.account_id
        ):
            await self.m.menu.send(msg, self.copy(msg)["failed"], [])
            return True
        async with self._lock:
            if account != self.m.svc.sync.account_id:
                return True
            key = self._key(self.m.svc.sync.account_id, chat, topic)
            row = self.file.load().get(key)
            if row and not self._valid(row):
                await self.m.menu.send(msg, self.copy(msg)["failed"], [])
                return True
            thread = self.m.svc.threads.get(str(row.get("thread") or "")) if row else None
            if row and thread is None:
                await self.m.menu.send(msg, self.copy(msg)["failed"], [])
                return True
            if thread is None:
                title = (
                    msg.chat_name.strip()[:100]
                    or " ".join(msg.text.split())[:60]
                    or self.copy(msg)["new"]
                )
                thread = self.m.svc.create_thread(title)
                self._put(msg, topic, thread, seeded=True)
                self.m.svc.sync.thread_changed(thread)
                await self.m.svc.sync.push()
                if account != self.m.svc.sync.account_id:
                    return True
            else:
                if not self.m.svc.sync._mine(thread):
                    await self.m.menu.send(msg, self.copy(msg)["failed"], [])
                    return True
                self.p.bind(msg, thread.id)
            if not msg.text.strip() and not msg.files:
                if msg.chat_name and msg.chat_name != thread.title:
                    self.m.svc.rename_thread(thread.id, msg.chat_name[:100])
                    self._put(msg, topic, thread, name=thread.title)
                await self.m.channels["telegram"].send(
                    msg.chat_id, self.copy(msg)["continued"].format(title=thread.title)
                )
                return True
        return False

    async def rename(self, thread_id: str) -> None:
        async with self._lock:
            account = self.m.svc.sync.account_id
            thread = self.m.svc.threads.get(thread_id)
            if thread is None or not self.m.svc.sync._mine(thread):
                return
            for key, row in list(self.file.load().items()):
                if (
                    not isinstance(row, dict)
                    or not self._valid(row)
                    or row.get("thread") != thread_id
                    or row.get("name") == thread.title
                ):
                    continue
                try:
                    await self.m.channels["telegram"]._call(
                        "editForumTopic",
                        {
                            "chat_id": row["chat"],
                            "message_thread_id": row["topic"],
                            "name": (" ".join(thread.title.split()) or COPY["ru"]["new"])[:100],
                        },
                    )
                except Exception as exc:
                    logger.warning("Telegram topic rename failed ({})", type(exc).__name__)
                    continue
                if account != self.m.svc.sync.account_id:
                    return
                rows = dict(self.file.load())
                rows[key] = {**row, "name": thread.title}
                self.file.save(rows)

    def defer(self, thread: str, endpoint: tuple[str, str], event: dict) -> bool:
        pending = self._backfill.get(endpoint)
        if pending is None or len(pending) >= 256 or self.p.selected(*endpoint) != thread:
            return False
        pending[str(event["mid"])] = dict(event)
        return True

    @asynccontextmanager
    async def _holding(self, destination: str, account: str) -> AsyncIterator[None]:
        endpoint = ("telegram", destination)
        self._backfill[endpoint] = {}
        try:
            yield
        finally:
            pending = self._backfill.pop(endpoint, {})
            if account == self.m.svc.sync.account_id:
                for event in pending.values():
                    await self.m._deliver_synced_channel(event)

    async def open(
        self, msg: Any, thread: Any, *, fresh: bool = False, announce: bool = True
    ) -> bool:
        if not self.enabled(msg.channel) or msg.is_group:
            return False
        if not self.m.menu.private_owner(msg) or not self.m.svc.sync._mine(thread):
            raise ValueError("topic owner unavailable")
        account = self.m.svc.sync.account_id

        def current() -> None:
            if account != self.m.svc.sync.account_id or not self.m.svc.sync._mine(thread):
                raise ValueError("account changed")

        c = self.copy(msg)
        chat = split_chat(msg.chat_id)[0]
        channel = self.m.channels["telegram"]
        async with self._lock:
            current()
            existing = next(
                (
                    r
                    for r in self.file.load().values()
                    if isinstance(r, dict)
                    and self._valid(r)
                    and r.get("chat") == chat
                    and (
                        r.get("thread") == thread.id
                        or (r.get("cid") and r.get("cid") == self.m.svc.sync.cid_of(thread.id))
                    )
                ),
                None,
            )
            if existing:
                topic = int(existing["topic"])
                try:
                    await channel._call(
                        "reopenForumTopic", {"chat_id": chat, "message_thread_id": topic}
                    )
                except Exception as exc:
                    if (
                        "topic_not_modified" not in str(exc).lower()
                        and "topic is not closed" not in str(exc).lower()
                    ):
                        raise
                current()
            else:
                created = await channel._call(
                    "createForumTopic",
                    {
                        "chat_id": chat,
                        "name": (" ".join(thread.title.split()) or c["new"])[:100],
                        "icon_color": (0xFFD67E, 0x8EEE98, 0xFB6F5F)[
                            int(hashlib.sha256(thread.id.encode()).hexdigest()[:2], 16) % 3
                        ],
                    },
                )
                topic = int(created["message_thread_id"])
                current()
                self._put(msg, topic, thread, seeded=False, seeded_mids=[])
                existing = self.file.load()[self._key(self.m.svc.sync.account_id, chat, topic)]
            current()
            destination = topic_chat(chat, topic)
            self._put(msg, topic, thread)
            history = list(thread.timeline.events)
            async with self._holding(destination, account):
                if existing.get("name") != thread.title:
                    await channel._call(
                        "editForumTopic",
                        {
                            "chat_id": chat,
                            "message_thread_id": topic,
                            "name": (" ".join(thread.title.split()) or c["new"])[:100],
                        },
                    )
                    current()
                    self._put(msg, topic, thread, name=thread.title)
                # Seed a small, clearly labelled history once, oldest first. Never replay tools/files.
                if not existing.get("seeded"):
                    seeded = list(existing.get("seeded_mids") or [])
                    recent = [
                        e
                        for e in history
                        if e.get("type") in {"user", "assistant"}
                        and str(e.get("text") or "").strip()
                    ][-8:]
                    for event in recent:
                        fingerprint = str(
                            event.get("mid")
                            or event.get("id")
                            or hashlib.sha256(
                                (str(event.get("ts")) + str(event.get("text"))).encode()
                            ).hexdigest()
                        )
                        if fingerprint in seeded:
                            continue
                        role = {
                            "en": ("You", "Lika"),
                            "zh": ("你", "Lika"),
                            "ru": ("Вы", "Лика"),
                        }.get(self.m.menu.language(msg), ("Вы", "Лика"))[
                            event["type"] == "assistant"
                        ]
                        text = str(event["text"]).strip()
                        body = text.encode("utf-16-le")[:6000].decode("utf-16-le", errors="ignore")
                        if body != text:
                            body += "…"
                        current()
                        await channel.send(destination, role + ":\n" + body)
                        current()
                        seeded.append(fingerprint)
                        self._put(msg, topic, thread, seeded_mids=seeded)
                        await asyncio.sleep(1.05)
                    self._put(msg, topic, thread, seeded=True)
                current()
                for event in history:
                    mid = str(event.get("mid") or "")
                    role = str(event.get("type") or "")
                    if mid and role in {"user", "assistant"}:
                        self.m.sync_receipts.mark(
                            self.m.sync_receipts.key("telegram", destination, mid, role)
                        )
                await channel.send(destination, c["continued"].format(title=thread.title))
        current()
        self.p.bind(replace(msg, chat_id=destination), thread.id, fresh=fresh)
        # A bot cannot force the Telegram client's active view to change.
        if announce and split_chat(msg.chat_id)[1] != topic:
            await self.m.menu.send(msg, c["open"].format(title=thread.title), [])
        return True
