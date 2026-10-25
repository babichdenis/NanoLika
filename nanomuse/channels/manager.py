"""The channel manager: one place where every chat app meets the agent.

It starts the channels that are switched on, decides who may talk to the bot (allowlist,
pairing), maps a chat to one conversation of the agent and carries the run back into the
chat as it happens: the reply grows in place where the vendor allows edits, tool activity
shows as a short status line, an approval the run asks for becomes a card (or a message)
answered with 允许 / 拒绝. Chats marked "deliver here" also get what the Muse does on its
own — check-ins, reminders, the results of background work — the same events the phone
turns into notifications.

The agent is not forked: a chat's message goes through :meth:`MuseService.send`, exactly
like a message typed in the app, and the events it produces are read off the service's bus.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from nanomuse.background import keep_task
from nanomuse.channels.approval_copy import approval_details, readable_approval
from nanomuse.channels.base import Channel, ChannelBus, ChannelStatus, InboundMessage
from nanomuse.channels.bot_copy import channel_text
from nanomuse.channels.chat_picker import ChatPicker
from nanomuse.channels.desktop_request import computer_request, select_computer
from nanomuse.channels.dingtalk import DingTalkChannel
from nanomuse.channels.feishu import FeishuChannel
from nanomuse.channels.link_start import handle_start_link
from nanomuse.channels.max import MaxChannel
from nanomuse.channels.menu import ChatMenu
from nanomuse.channels.store import ChannelSettingsStore, PairingStore
from nanomuse.channels.sync_delivery import SyncDeliveryReceipts
from nanomuse.channels.telegram import TelegramChannel
from nanomuse.channels.telegram_topics import physical_chat
from nanomuse.channels.wecom import WeComChannel
from nanomuse.channels.welcome import send_welcome_art
from nanomuse.hub.client import HubError
from nanomuse.logger import logger
from nanomuse.schema import Message
from nanomuse.server.events import MAIN_THREAD, new_id

if TYPE_CHECKING:
    from nanomuse.server.service import MuseService

REMOTE_TASK_S = 600

# How long a chat's own Cloud key is kept in memory before the bot door is asked again: a
# chat writing twice does not need two sign-ins, and the relay issues a key per chat anyway.
CLOUD_KEY_TTL_S = 600.0

CHANNEL_TYPES: tuple[type[Channel], ...] = (
    FeishuChannel,
    DingTalkChannel,
    WeComChannel,
    TelegramChannel,
    MaxChannel,
)

ALLOW_WORDS = frozenset(
    {
        "允许",
        "同意",
        "可以",
        "好",
        "好的",
        "允许。",
        "allow",
        "yes",
        "y",
        "ok",
        "approve",
        "go ahead",
        # Russian, as a person types it in a chat
        "да",
        "ок",
        "окей",
        "можно",
        "разрешаю",
        "разреши",
        "разрешить",
        "давай",
    }
)
DENY_WORDS = frozenset(
    {
        "拒绝",
        "不行",
        "不要",
        "不",
        "拒绝。",
        "deny",
        "no",
        "n",
        "reject",
        "stop",
        # Russian
        "нет",
        "нельзя",
        "запрещаю",
        "запрети",
        "откажи",
        "не надо",
        "стоп",
    }
)

# how often a growing reply is pushed to the chat (edits are rate-limited everywhere)
EDIT_INTERVAL_S = 1.0
# how long a first `/unlink` waits for the second one that confirms it
UNLINK_CONFIRM_SECONDS = 120.0
THREAD_PREFIX = "channel-"
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")

_TOOL_LABELS = {
    "web_search": ("Searching the web", "正在搜索", "Ищу в интернете"),
    "web_fetch": ("Reading a web page", "正在读网页", "Читаю страницу"),
    "files": ("Working with files", "正在处理文件", "Работаю с файлами"),
    "shell": ("Running a command", "正在运行命令", "Выполняю команду"),
    "python_execute": ("Running Python", "正在运行 Python", "Запускаю Python"),
    "read_emails": ("Reading email", "正在读邮件", "Читаю почту"),
    "send_email": ("Sending email", "正在发邮件", "Отправляю письмо"),
    "browser": ("Browsing", "正在浏览网页", "Открываю страницу"),
    "goals": ("Updating goals", "正在更新目标", "Обновляю цели"),
    "remember": ("Saving a memory", "正在记下来", "Запоминаю"),
    "recall": ("Recalling", "正在回忆", "Вспоминаю"),
}


def thread_id_for(channel: str, chat_id: str) -> str:
    """The conversation a chat maps to. Letters, digits, ``_ . -`` only: the id names a file
    in the threads folder, and a colon in a file name breaks on Windows."""
    safe = _SAFE.sub("_", chat_id).strip("._") or "chat"
    return f"{THREAD_PREFIX}{channel}-{safe[:80]}"


@dataclass
class _Live:
    """A reply under way in one chat: what has been shown, what is waiting to be shown."""

    channel: str
    chat_id: str
    stream_id: str = field(default_factory=lambda: new_id("s"))
    text: str = ""
    status: str = ""
    shown: str = ""
    sent_at: float = 0.0
    handle: asyncio.TimerHandle | None = None
    flushing: asyncio.Task[None] | None = None


class ChannelManager(ChannelBus):
    def __init__(self, svc: MuseService):
        self.svc = svc
        self.store = ChannelSettingsStore(svc.data_dir, svc.app.vault, svc.settings.source)
        self.pairing = PairingStore(svc.data_dir)
        self.types: dict[str, type[Channel]] = {c.name: c for c in CHANNEL_TYPES}
        self.channels: dict[str, Channel] = {}
        self.status: dict[str, ChannelStatus] = {n: ChannelStatus() for n in self.types}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[dict[str, Any]] | None = None
        self._pump: asyncio.Task[None] | None = None
        # thread id -> (channel, chat_id)
        self.routes: dict[str, tuple[str, str]] = {}
        # (channel, chat_id) -> when `/unlink` was first asked in this chat: the second ask
        # within the window is the confirmation. In memory on purpose: a restart forgets it,
        # and a confirmation nobody re-asks for is worse than asking again.
        self._unlink_pending: dict[tuple[str, str], float] = {}
        self.picker = ChatPicker(self)
        self.sync_receipts = SyncDeliveryReceipts(
            svc.settings.data_dir / "channels" / "sync-delivery.json"
        )
        self._live: dict[str, _Live] = {}
        # "channel:chat" -> approval ids waiting for a word from that chat (latest last)
        self._asks: dict[str, list[str]] = {}
        self._approval_scopes: dict[tuple[str, str, str], set[str]] = {}
        self._approval_details: dict[str, str] = {}
        # "channel:chat" -> the thread whose question was delivered there
        self._questions: dict[str, str] = {}
        self._artifacts: dict[str, list[str]] = {}
        self._typing: set[str] = set()
        self._tasks: set[asyncio.Task[Any]] = set()
        self.logins: dict[str, dict[str, Any]] = {}
        self.started = False
        self.menu = ChatMenu(self)

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self.started:
            return
        self.started = True
        self._loop = asyncio.get_running_loop()
        # chat -> (key, model, lapses at): the Cloud key of a chat that has no app (0.26)
        self._cloud_keys: dict[tuple[str, str], tuple[str, str, float]] = {}
        self._queue = self.svc.bus.subscribe()
        self._pump = asyncio.create_task(self._pump_loop(), name="channels-pump")
        for name in self.types:
            self._spawn(self.start_channel(name))

    async def stop(self) -> None:
        if not self.started:
            return
        self.started = False
        if self._pump is not None:
            self._pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pump
            self._pump = None
        if self._queue is not None:
            self.svc.bus.unsubscribe(self._queue)
            self._queue = None
        for task in list(self._tasks):
            task.cancel()
        for name in list(self.channels):
            await self.stop_channel(name)

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return keep_task(task)

    async def start_channel(self, name: str) -> ChannelStatus:
        """(Re)start one channel from its saved settings; the status says how it went."""
        cls = self.types[name]
        await self.stop_channel(name)
        cfg = self.store.config(name)
        if not cfg.enabled:
            return self._set(name, "off")
        if not cls.sdk_available():
            return self._set(name, "missing_sdk", cls.install_hint())
        missing = cls.missing(cfg.settings)
        if missing:
            labels = {f.key: f.label for f in cls.fields}
            return self._set(name, "unconfigured", ", ".join(labels.get(k, k) for k in missing))
        channel = cls(self.store.resolved(cfg))
        self.channels[name] = channel
        self._set(name, "connecting")
        try:
            await channel.start(self)
            try:
                await self.menu.configure(channel)
            except Exception:
                logger.warning("channel {}: command menu configuration failed", name)
        except Exception as exc:  # noqa: BLE001 — the vendor's words go to the app
            logger.warning("channel {} failed to start: {}", name, exc)
            self.channels.pop(name, None)
            with contextlib.suppress(Exception):
                await channel.stop()
            return self._set(name, "error", _short(exc))
        return self.status[name]

    async def stop_channel(self, name: str) -> None:
        channel = self.channels.pop(name, None)
        if channel is None:
            return
        try:
            await asyncio.wait_for(channel.stop(), 10)
        except Exception as exc:  # noqa: BLE001
            logger.debug("channel {} did not stop cleanly: {}", name, exc)
        self._set(name, "off")

    def _set(self, name: str, state: str, detail: str = "") -> ChannelStatus:
        status = ChannelStatus(state, detail)
        if self.status.get(name) != status:
            self.status[name] = status
            self.publish()
        return status

    def publish(self) -> None:
        """Tell the open apps the channels changed (they refetch ``/api/channels``)."""
        with contextlib.suppress(Exception):
            self.svc.bus.publish({"kind": "channels"})

    # ------------------------------------------------------------------ ChannelBus
    def _threadsafe(self, fn: Any, *args: Any) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            fn(*args)
        else:
            loop.call_soon_threadsafe(fn, *args)

    def inbound(self, msg: InboundMessage) -> None:
        self._threadsafe(lambda m: self._spawn(self._handle_inbound(m)), msg)

    def set_status(self, channel: str, state: str, detail: str = "") -> None:
        self._threadsafe(self._set, channel, state, detail)

    def action(self, channel: str, chat_id: str, sender_id: str, value: dict[str, Any]) -> None:
        self._threadsafe(
            lambda: self._spawn(self._handle_action(channel, chat_id, sender_id, value))
        )

    # ------------------------------------------------------------------ language
    @property
    def lang(self) -> str:
        language = (self.svc.settings.agent.language or "auto").lower()
        if language.startswith("zh"):
            return "zh"
        if language.startswith("en"):
            return "en"
        if language.startswith("ru"):
            return "ru"
        return "both"

    def say(self, en: str, zh: str, ru: str = "") -> str:
        """One system line in the language the agent was told to speak.

        "both" (the default, auto) stays English then Chinese, which is what a person who
        never chose a language gets. A line without a Russian wording falls back to
        English rather than to Chinese.
        """
        lang = self.lang
        if lang == "zh":
            return zh
        if lang == "en":
            return en
        if lang == "ru":
            return ru or en
        return f"{en}\n{zh}"

    @property
    def muse_name(self) -> str:
        return self.svc.profile.name or "nanoMuse"

    # ------------------------------------------------------------------ inbound
    def _allowed(self, msg: InboundMessage) -> bool:
        cfg = self.store.config(msg.channel)
        if "*" in cfg.allow_from or msg.sender_id in cfg.allow_from:
            return True
        return self.pairing.is_approved(msg.channel, msg.sender_id)

    async def _handle_inbound(self, msg: InboundMessage) -> None:
        channel = self.channels.get(msg.channel)
        if channel is None:
            return

        def linked_sender() -> None:
            self.pairing.add(
                msg.channel,
                msg.sender_id,
                sender_name=msg.sender_name,
                chat_id=physical_chat(msg.channel, msg.chat_id),
            )
            self.publish()

        if await handle_start_link(channel, msg, self.lang, linked_sender):
            return
        key = f"{msg.channel}:{msg.chat_id}"
        if not self._allowed(msg):
            cfg = self.store.config(msg.channel)
            if (
                getattr(cfg, "first_chat_claims", False)
                and (not msg.is_group)
                and (not self.pairing.approved(msg.channel))
            ):
                self.pairing.add(
                    msg.channel,
                    msg.sender_id,
                    sender_name=msg.sender_name,
                    chat_id=physical_chat(msg.channel, msg.chat_id),
                )
                self.publish()
                logger.info(
                    "channel {}: {} claimed the empty bot as its owner", msg.channel, msg.sender_id
                )
                await self._greet(msg.channel, msg.chat_id)
                return
            if msg.is_group:
                logger.debug(
                    "channel {}: ignoring group message from unknown {}", msg.channel, msg.sender_id
                )
                return
            code = self.pairing.request(
                msg.channel,
                msg.sender_id,
                sender_name=msg.sender_name,
                chat_id=physical_chat(msg.channel, msg.chat_id),
            )
            self.publish()
            await self._send(msg.channel, msg.chat_id, self.pairing_text(code))
            return
        cfg = self.store.config(msg.channel)
        if msg.is_group and cfg.group_policy == "mention" and (not msg.mentioned):
            return
        if not msg.is_group:
            self.pairing.touch(
                msg.channel,
                msg.sender_id,
                sender_name=msg.sender_name,
                chat_id=physical_chat(msg.channel, msg.chat_id),
            )
        if await self.picker.topics.inbound(msg):
            return
        if await self.menu.inbound(msg):
            return
        origin_thread = self._thread_for(msg)
        text = msg.text.strip()
        if text.lower().split("@")[0].split()[0:1] == ["/start"]:
            await self._greet(msg.channel, msg.chat_id)
            introduction = channel_text(self.lang, "welcome")
            if msg.channel == "max":
                introduction += "\n\n" + channel_text(self.lang, "limits")
            self._record_channel_reply(msg, introduction, thread_id=origin_thread)
            return
        command = text.split()[0].lower().split("@")[0] if text else ""
        if command in ("/unlink", "/отвязать", "/развязать"):
            await self._unlink_chat(msg)
            return
        if command in ("/help", "/status"):
            key = "help"
            if command == "/status":
                primary = getattr(self.svc.hub, "primary", lambda: None)()
                key = "online" if primary is not None else "offline"
            await self._send(
                msg.channel,
                msg.chat_id,
                channel_text(self.svc.settings.agent.language or "en", key),
            )
            self._record_channel_reply(msg, channel_text(self.lang, key), thread_id=origin_thread)
            return
        if not msg.is_group and text.casefold().strip(" .!。！👋🌿") in {
            "привет",
            "здравствуй",
            "здравствуйте",
            "добрый день",
            "hello",
            "hi",
            "你好",
        }:
            answer = channel_text(self.lang, "hello")
            await self._send(msg.channel, msg.chat_id, answer)
            self._record_channel_reply(msg, answer, thread_id=origin_thread)
            return
        requested = computer_request(text) if not msg.is_group else None
        if requested is not None:
            if not requested:
                await self._send(
                    msg.channel, msg.chat_id, channel_text(self.lang, "computer_usage")
                )
                return
            hub = self.svc.hub
            device = select_computer(hub.others(), hub.device_id)
            if device is None:
                await self._send(
                    msg.channel, msg.chat_id, channel_text(self.lang, "computer_unavailable")
                )
                return
            thread_id = origin_thread
            await self._echo_selected_user(thread_id, msg, requested)
            await self._send(msg.channel, msg.chat_id, channel_text(self.lang, "computer_started"))
            self._spawn(
                self._serve_from_device(
                    thread_id,
                    msg,
                    requested,
                    device,
                    fallback=False,
                    shared_cid=self.svc.sync.cid_of(thread_id)
                    if self.picker.selected(msg.channel, msg.chat_id) == thread_id
                    else "",
                )
            )
            return
        word = text.lower().strip(" .!。！")
        selected = self.picker.selected(msg.channel, msg.chat_id)
        if selected and self._asks.get(key) and (word in ALLOW_WORDS or word in DENY_WORDS):
            await self._send(
                msg.channel,
                msg.chat_id,
                self.say(
                    "Use the buttons on the permission request so the decision goes to the right chat.",
                    "请使用权限请求下方的按钮，以免将决定发送到另一对话。",
                    "Нажмите кнопку под запросом разрешения, чтобы решение относилось к нужному чату.",
                ),
            )
            return
        if self._asks.get(key) and (word in ALLOW_WORDS or word in DENY_WORDS):
            approval_id = self._asks[key][-1]
            await self._decide(msg.channel, msg.chat_id, approval_id, word in ALLOW_WORDS)
            return
        delivered = self._questions.get(key)
        if (
            delivered
            and (not selected or selected == delivered)
            and text
            and self.svc.ui.has_pending_question(delivered)
        ):
            self._questions.pop(key, None)
            self.svc.ui.answer_question(delivered, text)
            await self._send(
                msg.channel, msg.chat_id, self.say("Passed on.", "已转达。", "Передала.")
            )
            return
        if not delivered or not selected or selected == delivered:
            self._questions.pop(key, None)
        paths: list[str] = []
        for item in msg.files:
            try:
                paths.append(self.svc.save_upload(item.name, item.data).path)
            except ValueError as exc:
                await self._send(msg.channel, msg.chat_id, _short(exc))
        if not text and (not paths):
            return
        if msg.is_group and msg.sender_name and text:
            text = f"{msg.sender_name}: {text}"
        thread_id = origin_thread
        await self._echo_selected_user(thread_id, msg, text)
        self.picker.pins.setdefault(thread_id, set()).add((msg.channel, msg.chat_id))
        device = getattr(self.svc.hub, "primary", lambda: None)() if text and (not paths) else None
        if device is not None:
            self._spawn(
                self._serve_from_device(
                    thread_id,
                    msg,
                    text,
                    device,
                    shared_cid=self.svc.sync.cid_of(thread_id)
                    if self.picker.selected(msg.channel, msg.chat_id) == thread_id
                    else "",
                )
            )
            return
        # an account of its own in the relay when the chat is on Cloud, and `"!"` when that
        # account cannot be reached: then this turn is refused, never paid for by this machine
        cloud_key, cloud_model = await self._cloud_key_for(msg.channel, msg)
        if cloud_key == "!":
            return
        try:
            self.svc.send(
                thread_id,
                text,
                files=paths or None,
                once_key=cloud_key,
                once_model=cloud_model,
                language=self.menu.language(msg),
            )
        except (ValueError, PermissionError) as exc:
            await self._send(msg.channel, msg.chat_id, _short(exc))

    def _thread_for(self, msg: InboundMessage) -> str:
        selected = self.picker.selected(msg.channel, msg.chat_id)
        thread = self.svc.threads.get(selected)
        if thread is not None and self.svc.sync._mine(thread):
            if selected not in self.picker.pins:
                self.routes[selected] = (msg.channel, msg.chat_id)
            return selected
        if getattr(getattr(self.svc.settings, "channels", None), "shared_thread", False):
            thread_id = MAIN_THREAD
            self.routes[thread_id] = (msg.channel, msg.chat_id)
            if thread_id not in self.svc.threads:
                self.svc.timeline(thread_id)
            return thread_id
        thread_id = thread_id_for(msg.channel, msg.chat_id)
        self.routes[thread_id] = (msg.channel, msg.chat_id)
        if thread_id not in self.svc.threads:
            self.svc.timeline(thread_id)
            label = self.types[msg.channel].label
            who = msg.chat_name or (
                msg.sender_name or msg.sender_id if not msg.is_group else "group"
            )
            self.svc.rename_thread(
                thread_id,
                f"{label} · nanoLika"
                if msg.channel in {"telegram", "max"}
                else f"{label} · {who}"[:60],
            )
        return thread_id

    async def _handle_action(
        self, channel: str, chat_id: str, sender_id: str, value: dict[str, Any]
    ) -> None:
        if await self.menu.callback(channel, chat_id, sender_id, value):
            return
        approval_id = str(value.get("approval") or "")
        decision = str(value.get("decision") or "")
        if not approval_id or decision not in ("allow", "deny", "details"):
            return
        probe = InboundMessage(channel=channel, chat_id=chat_id, sender_id=sender_id)
        if not self._allowed(probe):
            return
        if decision == "details":
            if approval_id not in self._asks.get(f"{channel}:{chat_id}", []):
                return
            details = self._approval_details.get(
                approval_id,
                self.say(
                    "Details are not available for this request.",
                    "无法显示此请求的详情。",
                    "Подробности этого запроса недоступны.",
                ),
            )
            ch = self.channels.get(channel)
            expand = getattr(ch, "expand_approval", None)
            if expand is not None:
                try:
                    await expand(
                        chat_id,
                        approval_id,
                        details,
                        self.lang,
                        list(self._approval_scopes.get((channel, chat_id, approval_id), {"once"})),
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "channel {}: expanding approval failed: {}", channel, type(exc).__name__
                    )
            else:
                await self._send(channel, chat_id, details)
            return
        await self._decide(
            channel, chat_id, approval_id, decision == "allow", str(value.get("scope") or "once")
        )

    async def _decide(
        self, channel: str, chat_id: str, approval_id: str, approved: bool, scope: str = "once"
    ) -> None:
        card = (channel, chat_id, approval_id)
        if approval_id not in self._asks.get(f"{channel}:{chat_id}", []):
            return
        if scope not in self._approval_scopes.get(card, {"once"}):
            return
        key = f"{channel}:{chat_id}"
        label = self.types[channel].label
        ok = self.svc.decide(approval_id, approved, scope, f"from {label}")
        asks = self._asks.get(key)
        if asks and approval_id in asks:
            asks.remove(approval_id)
            if not asks:
                self._asks.pop(key, None)
        self._approval_scopes.pop(card, None)
        if ok and approved and scope != "once":
            await self._send(
                channel,
                chat_id,
                self.say(
                    "Permission saved for this action: " + scope,
                    "已记住此操作权限：" + scope,
                    "Разрешение для этого действия сохранено: "
                    + ("для этой беседы" if scope == "conversation" else "до отмены"),
                ),
            )
            return
        if ok:
            text = (
                self.say("Allowed.", "已允许。", "Разрешила.")
                if approved
                else self.say("Denied.", "已拒绝。", "Запретила.")
            )
        else:
            text = self.say(
                "That request is no longer waiting.",
                "这个请求已经不在等待了。",
                "Эта просьба уже не ждёт ответа.",
            )
        await self._send(channel, chat_id, text)

    def pairing_text(self, code: str) -> str:
        return self.say(
            f"Pairing code: {code}\nApprove it in nanoLika, Settings → Chat apps. The code lasts ten minutes.",
            f"配对码：{code}\n请在 nanoLika 的「设置 → 聊天入口」中确认，十分钟内有效。",
            f"Код для привязки: {code}\nПодтверди его в nanoLika, Настройки → Каналы. Код действует десять минут.",
        )

    # ------------------------------------------------------------------ outbound helpers
    async def _send(
        self, channel: str, chat_id: str, text: str, files: list[str] | None = None
    ) -> None:
        ch = self.channels.get(channel)
        if ch is None or not (text.strip() or files):
            return
        try:
            await ch.send(chat_id, text, files=files)
        except Exception as exc:  # noqa: BLE001 — a vendor refusal; the app shows it
            logger.warning("channel {}: send to {} failed: {}", channel, chat_id, exc)
            self._set(channel, "error", _short(exc))

    def _live_for(self, thread: str) -> _Live | None:
        route = self.routes.get(thread)
        if route is None:
            return None
        live = self._live.get(thread)
        if live is None:
            live = _Live(channel=route[0], chat_id=route[1])
            self._live[thread] = live
        return live

    def _schedule_flush(self, thread: str) -> None:
        live = self._live.get(thread)
        loop = self._loop
        if live is None or loop is None or live.handle is not None:
            return
        wait = max(0.0, EDIT_INTERVAL_S - (time.monotonic() - live.sent_at))
        live.handle = loop.call_later(wait, self._flush_now, thread)

    def _flush_now(self, thread: str) -> None:
        live = self._live.get(thread)
        if live is None:
            return
        live.handle = None
        if live.flushing is not None and not live.flushing.done():
            self._schedule_flush(thread)
            return
        live.flushing = self._spawn(self._flush(thread))

    async def _flush(self, thread: str) -> None:
        live = self._live.get(thread)
        channel = self.channels.get(live.channel) if live else None
        if live is None or channel is None or not channel.supports_edit:
            return
        content = live.text or (f"_{live.status}_" if live.status else "")
        if not content.strip() or content == live.shown:
            return
        live.sent_at = time.monotonic()
        try:
            await channel.stream(live.chat_id, live.stream_id, content, done=False)
            live.shown = content
        except Exception as exc:  # noqa: BLE001
            logger.debug("channel {}: live update failed: {}", live.channel, exc)

    async def _send_to_thread(self, thread: str, text: str) -> None:
        """A message to the chat a thread belongs to, when the route is still there."""
        route = self.routes.get(thread)
        if route is None:
            return
        await self._send(route[0], route[1], text)

    async def _finish(self, thread: str, text: str, *, files: list[str] | None = None) -> None:
        """The reply (one assistant bubble) is complete: the live message gets its final
        text, or a message is sent where nothing was streamed yet."""
        live = self._live.pop(thread, None)
        route = (live.channel, live.chat_id) if live is not None else self.routes.get(thread)
        if route is None:
            return
        channel = self.channels.get(route[0])
        if channel is None:
            return
        if live is not None:
            if live.handle is not None:
                live.handle.cancel()
            if live.flushing is not None and not live.flushing.done():
                with contextlib.suppress(Exception):
                    await live.flushing
        if thread in self.picker.remote_threads:
            for endpoint in (
                set(self.picker.targets(thread)) | self.picker.pins.get(thread, set()) | {route}
            ):
                self.picker.expect(thread, endpoint, "assistant", text)
        display_text = self.picker.reply_text(thread, route, text)
        try:
            if live is not None and live.shown and channel.supports_edit:
                await channel.stream(
                    live.chat_id, live.stream_id, display_text or live.shown, done=True
                )
                if files:
                    await channel.send(route[1], "", files=files)
            elif text.strip() or files:
                await channel.send(route[1], display_text, files=files)
        except Exception as exc:  # noqa: BLE001
            logger.warning("channel {}: reply to {} failed: {}", route[0], route[1], exc)
            self._set(route[0], "error", _short(exc))
        primary = (live.channel, live.chat_id) if live is not None else route
        targets = set(self.picker.targets(thread)) | self.picker.pins.pop(thread, set())
        for other in targets - {primary}:
            if text.strip():
                await self._send(other[0], other[1], self.picker.reply_text(thread, other, text))

    async def _set_typing(self, thread: str, on: bool) -> None:
        route = self.routes.get(thread)
        channel = self.channels.get(route[0]) if route else None
        if route is None or channel is None:
            return
        if on and thread in self._typing:
            return
        if not on and thread not in self._typing:
            return
        if on:
            self._typing.add(thread)
        else:
            self._typing.discard(thread)
        with contextlib.suppress(Exception):
            await channel.typing(route[1], on)

    # ------------------------------------------------------------------ the bus
    async def _pump_loop(self) -> None:
        assert self._queue is not None
        while True:
            msg = await self._queue.get()
            try:
                await self._route(msg)
            except Exception as exc:  # noqa: BLE001
                logger.warning("channels: could not route {}: {}", msg.get("kind"), exc)

    async def _route(self, msg: dict[str, Any]) -> None:
        kind = msg.get("kind")
        if kind == "delta":
            thread = str(msg.get("thread") or "")
            live = self._live_for(thread)
            if live is not None:
                live.text += str(msg.get("text") or "")
                self._schedule_flush(thread)
            return
        if kind == "stream_end":
            thread = str(msg.get("thread") or "")
            live = self._live.get(thread)
            if live is not None and msg.get("discard"):
                live.text = ""
            return
        if kind == "status":
            status = msg.get("status") or {}
            thread = str(status.get("thread") or "")
            if thread in self.routes:
                if status.get("state") == "idle":
                    await self._run_ended(thread)
                else:
                    await self._set_typing(thread, status.get("state") == "working")
            return
        if kind == "sync":
            await self.picker.topics.discover()
            return
        if kind == "thread":
            thread = str((msg.get("thread") or {}).get("id") or "")
            if thread:
                await self.picker.topics.rename(thread)
            return
        if kind == "event":
            await self._route_event(msg.get("event") or {})
            return
        if kind == "update":
            event = msg.get("update") or msg.get("event") or {}
            if event.get("type") == "approval" and event.get("status") != "pending":
                self._approval_details.pop(str(event.get("id") or ""), None)
                for card in list(self._approval_scopes):
                    if card[2] == event.get("id"):
                        self._approval_scopes.pop(card, None)
                for asks in self._asks.values():
                    if event.get("id") in asks:
                        asks.remove(event["id"])
            return

    async def _route_event(self, event: dict[str, Any]) -> None:
        if event.get("channel_static") and (not event.get("synced")):
            return
        kind = event.get("type")
        thread = str(event.get("thread") or "")
        ours = thread in self.routes
        if kind in ("user", "assistant") and event.get("synced"):
            await self._deliver_synced_channel(event)
            return
        if kind == "assistant":
            text = str(event.get("text") or "")
            if event.get("quiet"):
                return
            if ours:
                await self._finish(thread, text)
            elif event.get("source") == "background" and event.get("final") and text.strip():
                about = str(event.get("about") or "")
                await self.deliver(f"{about}\n\n{text}" if about else text, exclude_thread=thread)
            return
        if kind == "notice" and ours and (event.get("level") == "error"):
            text = str(event.get("text") or "").strip()
            if event.get("code") == "allowance_exhausted":
                text = self.say(
                    "The free allowance of this account is used up. Sign in to the app to top it up, or invite a friend for more.",
                    "这个账户的免费额度用完了。登录应用充值，或邀请朋友获得更多额度。",
                    "Бесплатный запас этого аккаунта закончился. Войди в приложение, чтобы пополнить, или пригласи друга и получи ещё.",
                ) + (f"\n\n{text}" if text else "")
            if text:
                await self._send_to_thread(thread, text)
            return
        if kind == "tool" and ours and (event.get("status") == "running"):
            await self._set_typing(thread, True)
            live = self._live_for(thread)
            if live is not None:
                live.status = self._tool_label(event)
                self._schedule_flush(thread)
            return
        if kind == "approval" and event.get("status") == "pending":
            self._approval_details[str(event.get("id") or "")] = approval_details(event)
            text = self.approval_text(event)
            approval_id = str(event.get("id") or "")
            if ours:
                await self._ask(
                    *self.routes[thread], approval_id, text, event.get("grant_options") or ["once"]
                )
            else:
                for channel, chat_id in self._targets(exclude_thread=thread):
                    await self._ask(
                        channel, chat_id, approval_id, text, event.get("grant_options") or ["once"]
                    )
            return
        if kind == "question" and event.get("status") == "pending":
            question = str(event.get("text") or "")
            if ours:
                await self._set_typing(thread, False)
                await self._finish(thread, question)
            else:
                for channel, chat_id in self._targets(exclude_thread=thread):
                    self._questions[f"{channel}:{chat_id}"] = thread
                    await self._send(
                        channel,
                        chat_id,
                        self.say(
                            f"{self.muse_name} asks: {question}\n(Reply here to answer.)",
                            f"{self.muse_name} 问：{question}\n（直接回复即可。）",
                            f"{self.muse_name} спрашивает: {question}\n(Ответь здесь.)",
                        ),
                    )
            return
        if kind == "notice" and ours and (event.get("level") == "warn"):
            await self._finish(thread, str(event.get("text") or ""))
            return
        if kind == "artifact" and ours and event.get("path"):
            self._artifacts.setdefault(thread, []).append(str(event["path"]))

    async def _run_ended(self, thread: str) -> None:
        live = self._live.get(thread)
        files = self._artifacts.pop(thread, [])
        paths: list[str] = []
        for rel in files:
            with contextlib.suppress(Exception):
                paths.append(str(self.svc.resolve_workspace_path(rel)))
        if live is not None and (live.text.strip() or live.shown):
            await self._finish(thread, live.text or live.shown, files=paths or None)
        else:
            self._live.pop(thread, None)
            if paths:
                await self._finish(thread, "", files=paths)
        await self._set_typing(thread, False)

    def _tool_label(self, event: dict[str, Any]) -> str:
        title = str(event.get("title") or "").strip()
        if title:
            return title
        tool = str(event.get("tool") or "")
        en, zh, ru = _TOOL_LABELS.get(
            tool, (f"Using {tool}", f"正在使用 {tool}", f"Работаю: {tool}")
        )
        lang = self.lang
        if lang == "ru":
            return ru
        return zh if lang == "zh" else en

    def approval_text(self, event: dict[str, Any]) -> str:
        if self.lang == "ru":
            return readable_approval(event, self.muse_name)
        summary = str(event.get("summary") or event.get("tool") or "")
        purpose = str(event.get("purpose") or "")
        why = f"\n({purpose})" if purpose else ""
        return self.say(
            f"{self.muse_name} needs your OK: {summary}{why}\nReply allow / deny.",
            f"{self.muse_name} 需要你确认：{summary}{why}\n回复「允许」或「拒绝」。",
        )

    async def _ask(
        self,
        channel: str,
        chat_id: str,
        approval_id: str,
        text: str,
        scopes: list[str] | None = None,
    ) -> None:
        ch = self.channels.get(channel)
        if ch is None or not approval_id:
            return
        allowed = {"once"} | (set(scopes or []) & {"conversation", "always"})
        self._asks.setdefault(f"{channel}:{chat_id}", []).append(approval_id)
        self._approval_scopes[(channel, chat_id, approval_id)] = allowed
        try:
            scoped = getattr(ch, "ask_scoped", None)
            if scoped is not None:
                await scoped(chat_id, approval_id, text, self.lang, list(allowed))
            else:
                await ch.ask(chat_id, approval_id, text, self.lang)
        except Exception as exc:  # noqa: BLE001
            logger.warning("channel {}: could not send approval: {}", channel, type(exc).__name__)
            self._set(channel, "error", _short(exc))

    def _targets(self, exclude_thread: str = "") -> list[tuple[str, str]]:
        skip = self.routes.get(exclude_thread)
        return [
            (channel, chat_id)
            for channel, chat_id in self.pairing.deliver_targets()
            if channel in self.channels and (channel, chat_id) != skip
        ]

    async def deliver(self, text: str, *, exclude_thread: str = "") -> int:
        """Send ``text`` to every chat marked "deliver here"; returns how many got it."""
        n = 0
        for channel, chat_id in self._targets(exclude_thread):
            await self._send(channel, chat_id, text)
            n += 1
        return n

    # ------------------------------------------------------------------ settings & pairing
    def view(self) -> dict[str, Any]:
        channels = []
        for name, cls in self.types.items():
            cfg = self.store.config(name)
            status = self.status.get(name) or ChannelStatus()
            if name not in self.channels:
                if not cfg.enabled:
                    status = ChannelStatus("off")
                elif not cls.sdk_available():
                    status = ChannelStatus("missing_sdk", cls.install_hint())
                elif cls.missing(cfg.settings):
                    labels = {f.key: f.label for f in cls.fields}
                    status = ChannelStatus(
                        "unconfigured",
                        ", ".join(labels.get(k, k) for k in cls.missing(cfg.settings)),
                    )
                elif status.state not in ("error", "connecting"):
                    status = ChannelStatus("off")
            fields = []
            for f in cls.fields:
                item = f.to_dict()
                if f.kind == "secret":
                    item["has_value"] = self.store.has_secret(cfg, f.key)
                else:
                    value = cfg.settings.get(f.key, f.default)
                    item["value"] = value if isinstance(value, bool) else str(value or "")
                fields.append(item)
            paired = [
                {
                    "sender_id": sender_id,
                    "sender_name": entry.get("sender_name", ""),
                    "chat_id": entry.get("chat_id", sender_id),
                    "deliver": bool(entry.get("deliver")),
                    "approved_at": entry.get("approved_at"),
                    "thread": thread_id_for(name, str(entry.get("chat_id") or sender_id)),
                }
                for sender_id, entry in self.pairing.approved(name).items()
            ]
            channels.append(
                {
                    "name": name,
                    "label": cls.label,
                    "enabled": cfg.enabled,
                    "status": status.to_dict(),
                    "sdk_available": cls.sdk_available(),
                    "install": cls.install_hint(),
                    "console_url": cls.console_url,
                    "supports_edit": cls.supports_edit,
                    "fields": fields,
                    "allow_from": cfg.allow_from,
                    "group_policy": cfg.group_policy,
                    "paired": paired,
                    "login": name in self.logins,
                }
            )
        return {"channels": channels, "pending": self.pairing.pending()}

    async def update(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        if name not in self.types:
            raise KeyError(name)
        was_enabled = self.store.config(name).enabled
        cfg = self.store.update(
            self.types[name],
            enabled=body.get("enabled"),
            values=body.get("settings"),
            allow_from=body.get("allow_from"),
            group_policy=body.get("group_policy"),
        )
        # the allowlist and the group policy are read per message; only credentials and the
        # switch itself touch the connection
        reconnect = bool(body.get("settings")) or cfg.enabled != was_enabled
        if not cfg.enabled:
            await self.stop_channel(name)
        elif reconnect or name not in self.channels:
            await self.start_channel(name)
        self.publish()
        return self.view()

    async def reload(self) -> dict[str, Any]:
        """Settings or pairings were changed on disk (the CLI): start what is on now."""
        for name in self.types:
            cfg = self.store.config(name)
            if cfg.enabled and name not in self.channels:
                await self.start_channel(name)
            elif not cfg.enabled and name in self.channels:
                await self.stop_channel(name)
        self.publish()
        return self.view()

    async def approve(self, code: str) -> dict[str, Any] | None:
        entry = self.pairing.approve(code)
        if entry is None:
            return None
        self.publish()
        paired_message = InboundMessage(
            channel=entry["channel"],
            chat_id=str(entry.get("chat_id") or entry["sender_id"]),
            sender_id=str(entry["sender_id"]),
            sender_name=str(entry.get("sender_name") or ""),
        )
        thread_id = self._thread_for(paired_message)
        self.svc.sync.thread_changed(self.svc.threads[thread_id])
        await self._greet(entry["channel"], str(entry.get("chat_id") or entry["sender_id"]))
        return entry

    async def deny(self, code: str) -> dict[str, Any] | None:
        info = self.pairing.deny(code)
        if info is not None:
            self.publish()
        return info

    def set_deliver(self, name: str, sender_id: str, on: bool) -> dict[str, Any] | None:
        entry = self.pairing.set_deliver(name, sender_id, on)
        if entry is not None:
            self.publish()
        return entry

    def remove_chat(self, name: str, sender_id: str) -> bool:
        ok = self.pairing.remove(name, sender_id)
        if ok:
            self.publish()
        return ok

    async def test(self, name: str, chat_id: str = "") -> dict[str, Any]:
        """Send a test message to a paired chat (the first one when none is given), or
        check the credentials when the channel has no paired chat yet."""
        channel = self.channels.get(name)
        if channel is None:
            status = self.status.get(name) or ChannelStatus()
            raise ValueError(
                status.detail or f"{self.types[name].label} is not running; switch it on first."
            )
        if not chat_id:
            paired = self.pairing.approved(name)
            first = next(iter(paired.values()), None)
            chat_id = str(first.get("chat_id") or "") if first else ""
        if not chat_id:
            about = await channel.verify()
            return {"ok": True, "detail": about or "The credentials work."}
        await channel.send(
            chat_id,
            self.say(
                f"Test message from {self.muse_name}: this chat is connected.",
                f"来自 {self.muse_name} 的测试消息：这个聊天已经连上了。",
            ),
        )
        return {"ok": True, "detail": f"Sent to {chat_id}."}

    # ------------------------------------------------------------------ Feishu QR login
    async def login_begin(self, name: str, domain: str = "feishu") -> dict[str, Any]:
        if name != "feishu":
            raise ValueError(
                "Only Feishu has a scan-to-create login; the others take an id and a secret."
            )
        from nanomuse.channels import feishu

        session = await asyncio.to_thread(feishu.login_begin, domain, self.muse_name)
        self.logins[name] = {**session, "domain": domain, "started": time.time()}
        self.publish()
        return dict(session)

    async def login_poll(self, name: str, device_code: str) -> dict[str, Any]:
        session = self.logins.get(name)
        if session is None or session.get("device_code") != device_code:
            raise KeyError(device_code)
        from nanomuse.channels import feishu

        result = await asyncio.to_thread(feishu.login_poll, device_code, session["domain"])
        if result.get("status") == "succeeded":
            self.logins.pop(name, None)
            self.store.update(
                self.types[name],
                enabled=True,
                values={
                    "app_id": result["app_id"],
                    "app_secret": result["app_secret"],
                    "domain": result.get("domain") or session["domain"],
                },
            )
            await self.start_channel(name)
            self.publish()
            return {"status": "succeeded", "app_id": result["app_id"]}
        if result.get("status") == "failed":
            self.logins.pop(name, None)
            self.publish()
        if time.time() - session.get("started", 0) > session.get("expires_in", 600):
            self.logins.pop(name, None)
            self.publish()
            return {"status": "failed", "error": "expired"}
        return result

    async def _echo_selected_user(self, thread: str, msg: InboundMessage, text: str) -> None:
        if not text:
            return
        self.picker.title_first_request(thread, text)
        label = self.types[msg.channel].label
        for endpoint in self.picker.targets(thread):
            if endpoint != (msg.channel, msg.chat_id):
                await self._send(
                    endpoint[0],
                    endpoint[1],
                    label + " · " + self.say("You", "你", "Вы") + "\n" + text,
                )

    async def _cloud_key_for(self, channel: str, msg: InboundMessage) -> tuple[str, str]:
        """The Cloud key and model this chat spends with, if the chat is on Cloud.

        A chat that has no app yet is an account of its own in the relay: the bot door
        (`POST /v1/bot/signin`, the shared secret in `cloud_bot_token`) answers with that
        account's key and the model its menu recommends, and this one turn is charged to
        the person instead of to this machine. Settings: `cloud_per_chat = true`,
        `cloud_url` (the relay) and `cloud_bot_token`.

        The key is kept for a few minutes in memory: a chat writing twice does not need two
        sign-ins, and a key that lapses is replaced by the next message.

        `("", "")` has one meaning: this chat is not on Cloud, so it runs on the model this
        machine is configured with, which is the mode the person chose. A Cloud chat whose
        own account cannot be reached gets `"!"` and the caller stops: the key of this
        machine never pays for a turn that was meant for the relay, and the chat is told
        why instead of seeing nothing happen."""
        settings = self.store.resolved(self.store.config(channel))
        if str(settings.get("cloud_per_chat") or "").lower() not in ("1", "true", "yes"):
            return "", ""
        url = str(settings.get("cloud_url") or "").strip().rstrip("/")
        token = str(settings.get("cloud_bot_token") or "").strip()
        if not url or not token:
            logger.warning(
                "channel {}: cloud_per_chat is on but cloud_url or cloud_bot_token is empty",
                channel,
            )
            await self._send(
                channel,
                msg.chat_id,
                self.say(
                    "This chat is set up for Cloud, but the server address or the bot token"
                    " is missing. Ask the owner to finish the setup.",
                    "这个聊天已设置为云端，但缺少服务器地址或机器人令牌。请让所有者完成设置。",
                    "Этот чат настроен на облако, но не задан адрес сервера или токен бота."
                    " Попроси владельца закончить настройку.",
                ),
            )
            return "!", ""
        cache_key = (channel, msg.sender_id)
        held = self._cloud_keys.get(cache_key)
        if held is not None and held[2] > time.time():
            return held[0], held[1]
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                answer = await client.post(
                    f"{url}/v1/bot/signin",
                    json={
                        # the account is the pair (messenger, id): the same number on Telegram
                        # and on MAX is two accounts, so the messenger is named, never guessed
                        "channel": channel,
                        "chat_id": msg.sender_id,
                        "device": f"{channel}:{physical_chat(channel, msg.chat_id)}",
                    },
                    headers={"X-Bot-Token": token},
                )
        except httpx.HTTPError as exc:
            logger.warning("channel {}: the bot door is unreachable: {}", channel, exc)
            await self._send(
                channel,
                msg.chat_id,
                self.say(
                    "The server is not answering, so this chat cannot run right now. Try"
                    " again in a minute.",
                    "服务器暂时没有回应，这个聊天现在无法运行。请稍后再试。",
                    "Сервер не отвечает, поэтому этот чат сейчас не может работать. Попробуй"
                    " через минуту.",
                ),
            )
            return "!", ""
        body: dict[str, Any] = {}
        with contextlib.suppress(ValueError):
            parsed = answer.json()
            if isinstance(parsed, dict):
                body = parsed
        if answer.status_code != 200:
            error = body.get("error") if isinstance(body.get("error"), dict) else {}
            code = str(error.get("code") or "")
            text = str(error.get("message") or "")
            logger.warning(
                "channel {}: the bot door refused ({}): {}",
                channel,
                answer.status_code,
                code or text,
            )
            await self._cloud_no_account(channel, msg.chat_id)
            return "!", ""
        key = str(body.get("api_key") or "")
        models = body.get("models") or []
        model = ""
        if isinstance(models, list) and models and isinstance(models[0], dict):
            model = str(models[0].get("id") or "")
        if not key:
            # a door that opens nothing is a refusal too: an empty key would otherwise send
            # this turn with the key of this machine
            logger.warning("channel {}: the bot door answered without a key", channel)
            await self._cloud_no_account(channel, msg.chat_id)
            return "!", ""
        self._cloud_keys[cache_key] = (key, model, time.time() + CLOUD_KEY_TTL_S)
        return key, model

    async def _cloud_no_account(self, channel: str, chat_id: str) -> None:
        """Say that the relay has no account for this chat: nothing runs for it."""
        await self._send(
            channel,
            chat_id,
            self.say(
                "This chat cannot be used just yet: the server did not open an account for"
                " it. Try again later.",
                "这个聊天暂时还不能用：服务器没有为它开账户。请稍后再试。",
                "Этот чат пока нельзя использовать: сервер не открыл для него аккаунт."
                " Попробуй позже.",
            ),
        )

    async def _serve_from_device(
        self,
        thread: str,
        msg: InboundMessage,
        text: str,
        device: dict[str, Any],
        *,
        fallback: bool = True,
        shared_cid: str = "",
    ) -> None:
        """A chat's turn handed to the device the settings name as the one that serves the
        chats (`hub.primary`): the words go over the hub as a `task`, the device's progress
        shows as the live status, its answer closes the bubble. A device that cannot take it
        (offline, busy, refused) steps aside and the Muse on this machine answers here.

        The device's own approvals are not decided from the chat: the card names itself and
        the task lapses on the device's side, and nothing here is ever allowed silently.
        """
        if shared_cid and thread in self.picker.remote_threads:
            await self._send(
                msg.channel,
                msg.chat_id,
                self.say(
                    "This chat is already working. Please send the next request after its reply.",
                    "此对话仍在处理中，请收到回复后再发送下一条请求。",
                    "В этом чате ещё идёт работа. Отправьте следующую просьбу после ответа.",
                ),
            )
            return
        if shared_cid and "shared_chat" not in (device.get("actions") or []):
            if fallback:
                self.svc.send(thread, text, language=self.menu.language(msg))
            else:
                await self._finish(
                    thread,
                    self.say(
                        "Update the computer app to run tasks in this shared chat.",
                        "请更新电脑应用，以便在此共享对话中执行任务。",
                        "Для работы на компьютере в выбранном общем чате нужно обновить приложение. Задачу в другой чат не переношу.",
                    ),
                )
            return
        self.picker.remote_threads.add(thread)
        await self._set_typing(thread, True)
        try:
            hub = self.svc.hub
            name = str(device.get("name") or device.get("id") or "")
            live = self._live_for(thread)
            if live is not None:
                live.status = self.say(f"on {name}", f"在 {name} 上", f"работает {name}")
                self._schedule_flush(thread)

            async def on_event(body: dict[str, Any]) -> None:
                stage = body.get("stage")
                lv = self._live_for(thread)
                if lv is None:
                    return
                if stage == "text" and body.get("text"):
                    lv.text = str(body.get("text") or "")
                    self._schedule_flush(thread)
                elif stage == "tool":
                    lv.status = f"{str(body.get('name') or 'tool')}: {str(body.get('summary') or '')}".strip(
                        ": "
                    )
                    self._schedule_flush(thread)
                elif stage == "approval":
                    preview = str(body.get("preview") or body.get("reason") or "")[:120]
                    lv.status = self.say(
                        f"{name} asks permission: {preview}",
                        f"{name} 请求许可: {preview}",
                        f"{name} спрашивает разрешение: {preview}",
                    )
                    self._schedule_flush(thread)

            mode = self.menu.preferences.mode(thread)
            if mode:
                try:
                    applied = await hub.call(
                        str(device.get("id") or ""),
                        "channel_permissions",
                        {
                            "conversation": thread,
                            "sync_cid": shared_cid,
                            "channel": msg.channel,
                            "mode": mode,
                        },
                        timeout=12,
                    )
                    if applied.get("mode") != mode:
                        raise ValueError("permission mode was not applied")
                except Exception:
                    from nanomuse.channels.menu import COPY

                    await self._finish(thread, COPY[self.menu.language(msg)]["remote_old"])
                    return
            try:
                if shared_cid:
                    for endpoint in set(self.picker.targets(thread)) | {(msg.channel, msg.chat_id)}:
                        self.picker.expect(thread, endpoint, "user", text)
                answer = await hub.call(
                    str(device.get("id") or ""),
                    "task",
                    {
                        "text": text,
                        "from": hub.device_name,
                        "conversation": thread,
                        "sync_cid": shared_cid,
                        "channel": msg.channel,
                        "language": self.menu.language(msg),
                    },
                    timeout=REMOTE_TASK_S,
                    on_event=on_event,
                )
            except HubError as exc:
                if shared_cid:
                    await self._finish(
                        thread,
                        self.say(
                            "The computer has not completed this request. I will not start it again automatically; check this chat before retrying.",
                            "电脑尚未完成此请求。为避免重复执行，不会自动重试，请先查看此对话。",
                            "Компьютер пока не завершил запрос. Повторно задачу не запускаю, чтобы не выполнить её дважды. Проверьте этот чат перед повтором.",
                        ),
                    )
                    return
                if not fallback:
                    await self._finish(thread, channel_text(self.lang, "computer_failed"))
                    return
                self._live.pop(thread, None)
                route = self.routes.get(thread)
                if route is not None:
                    await self._send(
                        route[0],
                        route[1],
                        self.say(
                            f"{name}: {exc.message}. Answering here.",
                            f"{name}: {exc.message}. 由我回答。",
                            f"{name}: {exc.message}. Отвечаю сама.",
                        ),
                    )
                try:
                    self.svc.send(thread, text, language=self.menu.language(msg))
                except (ValueError, PermissionError) as error:
                    await self._send(msg.channel, msg.chat_id, _short(error))
                return
            final = str(answer.get("text") or "").strip()
            if final and (not fallback):
                self._record_channel_reply(msg, final, thread_id=thread)
            await self._finish(
                thread,
                final
                or self.say(
                    "(the device sent no answer)", "（设备没有回答）", "（устройство не ответило）"
                ),
            )
        finally:
            self.picker.remote_threads.discard(thread)
            for pending in self.picker.deferred.pop(thread, {}).values():
                await self._deliver_synced_channel(pending)
            await self._set_typing(thread, False)

    def _record_channel_reply(
        self, msg: InboundMessage, answer: str, *, thread_id: str = ""
    ) -> None:
        """Keep deterministic replies in the same synced chat, without running a model."""
        if msg.channel not in {"telegram", "max"}:
            return
        tid = thread_id or self._thread_for(msg)
        self.svc.ui.emit({"type": "user", "thread": tid, "text": msg.text, "channel_static": True})
        event = self.svc.ui.emit(
            {
                "type": "assistant",
                "thread": tid,
                "text": answer,
                "final": True,
                "channel_static": True,
            }
        )
        thread = self.svc.threads[tid]
        if not thread.busy:
            thread.agent.messages.extend([Message.user(msg.text), Message.assistant(answer)])
            thread.agent._save_session()
        thread.updated_at = event["ts"]
        self.svc._save_index()
        self.svc.bus.publish({"kind": "thread", "thread": thread.meta()})
        self.svc.sync.thread_changed(thread)

    async def _deliver_synced_channel(self, event: dict[str, Any]) -> None:
        role = str(event.get("type") or "")
        thread = str(event.get("thread") or "")
        mid = str(event.get("mid") or "")
        source = str(event.get("via_device") or "")
        if role not in {"user", "assistant"} or not mid or not source:
            return
        if source == self.svc.hub.device_id or event.get("quiet"):
            return
        if not self.sync_receipts.eligible(str(event.get("ts") or "")):
            return
        if role == "assistant" and thread in self.picker.remote_threads:
            pending = self.picker.deferred.setdefault(thread, {})
            if len(pending) < 100:
                pending[mid] = dict(event)
            return
        await self.picker.topics.ensure_for_event(event)
        targets = set(self.picker.targets(thread))
        # Existing fixed chats remain paired until their owner explicitly chooses another.
        for channel in ("telegram", "max"):
            if channel not in self.channels or not self.store.config(channel).enabled:
                continue
            for sender, entry in self.pairing.approved(channel).items():
                chat = str(entry.get("chat_id") or sender)
                if (
                    not self.picker.selected(channel, chat)
                    and thread_id_for(channel, chat) == thread
                ):
                    targets.add(self.picker.topics.redirect(thread, channel, chat))
        body = str(event.get("text") or "").strip()
        if not body:
            return
        if role == "user":
            body = self.say("💻 You", "💻 你", "💻 Вы") + "\n" + body
        for channel, chat in targets:
            if self.picker.topics.defer(thread, (channel, chat), event):
                continue
            key = self.sync_receipts.key(channel, chat, mid, role)
            if self.picker.consume(
                thread, (channel, chat), role, str(event.get("text") or "").strip()
            ):
                self.sync_receipts.mark(key)
                continue
            if self.sync_receipts.delivered(key):
                continue
            for attempt in range(3):
                # Re-check after a retry: the person may have switched away.
                current = self.picker.selected(channel, chat)
                if current and current != thread:
                    break
                paired = any(
                    str(e.get("chat_id") or sender) == physical_chat(channel, chat)
                    for sender, e in self.pairing.approved(channel).items()
                )
                if (
                    not paired
                    or channel not in self.channels
                    or not self.store.config(channel).enabled
                ):
                    break
                try:
                    await self.channels[channel].send(chat, body)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    if attempt < 2:
                        await asyncio.sleep(attempt + 1)
                        continue
                    self._set(
                        channel,
                        "error",
                        self.say(
                            "Could not deliver a synced message.",
                            "同步消息发送失败。",
                            "Не удалось доставить сообщение из приложения.",
                        ),
                    )
                else:
                    self.sync_receipts.mark(key)
                break

    def welcome_links(self, channel: str) -> list[tuple[str, str]]:
        """Where a chat can go next, as ``(label, url)``, from the channel's own settings.

        ``site_url`` is the public page, ``invite_url`` the link a person shares (the app
        appends the code), and ``webapp_url`` the app itself when the runtime is reachable
        over HTTPS. Nothing is offered that was not set.
        """
        cfg = self.store.config(channel)
        out: list[tuple[str, str]] = []
        site = str(cfg.settings.get("site_url") or "").strip()
        invite = str(cfg.settings.get("invite_url") or "").strip()
        webapp = str(cfg.settings.get("webapp_url") or "").strip()
        if site:
            out.append((self.say("Website", "网站", "Сайт"), site))
        if invite:
            out.append((self.say("Invite a friend", "邀请朋友", "Пригласить друга"), invite))
        if webapp:
            out.append((self.say("Open the app", "打开应用", "Открыть приложение"), webapp))
        return out

    async def _unlink_chat(self, msg: InboundMessage) -> None:
        """`/unlink`: this chat lets go of the account it was tied to.

        Asked twice on purpose. Unlinking is not destructive (the account and its history stay
        in the app) but it is not undoable either: the chat stops reaching that account, its
        next message makes a fresh one with a fresh allowance, and tying it back needs a new
        code from the app. A person typing this by accident should get a question, and a person
        who has no app at all should still have a way out, which is why this lives in the chat
        rather than in the app alone."""
        ch = self.channels.get(msg.channel)
        link = getattr(ch, "chat_link", None) if ch is not None else None
        if link is None:
            await self._send(msg.channel, msg.chat_id, channel_text(self.lang, "unlink_offline"))
            return
        key = (msg.channel, msg.chat_id)
        asked = self._unlink_pending.get(key)
        if asked and time.monotonic() - asked < UNLINK_CONFIRM_SECONDS:
            self._unlink_pending.pop(key, None)
            try:
                answer = await link().release(msg.sender_id or msg.chat_id, self.lang)
            except Exception as exc:  # noqa: BLE001 — a command must never break the chat
                logger.warning("channel {}: unlink failed: {}", msg.channel, type(exc).__name__)
                answer = channel_text(self.lang, "unlink_offline")
            await self._send(msg.channel, msg.chat_id, answer)
            return
        self._unlink_pending[key] = time.monotonic()
        await self._send(msg.channel, msg.chat_id, channel_text(self.lang, "unlink_confirm"))

    async def _greet(self, channel: str, chat_id: str) -> None:
        """The greeting, then the links the channel was configured with."""
        greeting = channel_text(self.lang, "welcome")
        if channel == "max":
            greeting += "\n\n" + channel_text(self.lang, "limits")
        ch = self.channels.get(channel)
        if ch is not None:
            try:
                await send_welcome_art(ch, chat_id)
            except Exception:
                logger.warning("channel {}: welcome artwork unavailable; sending text", channel)
        await self._send(channel, chat_id, greeting)
        buttons = self.welcome_links(channel)
        if not buttons:
            return
        ch = self.channels.get(channel)
        if ch is None:
            return
        try:
            await ch.links(
                chat_id, buttons, self.say("Where to go next:", "还能去哪儿：", "Куда ещё можно:")
            )
        except Exception as exc:
            logger.warning("channel {}: could not send links: {}", channel, exc)

    def greeting(self) -> str:
        """An introduction without model calls or claims about connected services."""
        return channel_text(self.lang, "welcome")


def _short(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    return text.splitlines()[0][:300]


__all__ = ["CHANNEL_TYPES", "ChannelManager", "thread_id_for"]
