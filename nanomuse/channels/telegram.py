"""Telegram, over the Bot API with plain HTTPS long polling (``httpx`` only, no SDK).

``getUpdates`` with a long timeout brings messages in; replies are ``sendMessage`` and are
edited in place with ``editMessageText`` while they stream. Approvals are two inline buttons.
Nothing needs a public URL; a proxy can be set for networks where ``api.telegram.org`` is
out of reach.

Setup: in Telegram talk to @BotFather → ``/newbot`` → copy the token. Direct messages work
at once. For groups, add the bot to the group; with BotFather's *Group Privacy* on (the
default) the bot only hears messages that @-mention it or reply to it, which matches the
``mention`` policy.
"""

from __future__ import annotations

import asyncio
import mimetypes
from contextlib import suppress
from html import escape
from pathlib import Path
from typing import Any

import httpx

from nanomuse.channels import telegram_format
from nanomuse.channels.approval_choices import choices
from nanomuse.channels.base import (
    Channel,
    ChannelBus,
    Field,
    InboundFile,
    InboundMessage,
    json_object,
)
from nanomuse.channels.link_code import ChatLink, answer_code
from nanomuse.channels.telegram_topics import split_chat, topic_chat
from nanomuse.logger import logger

API = "https://api.telegram.org"
# the two words on an approval card, by the language the run answers in
_BUTTONS = {
    "ru": ("Разрешить", "Запретить"),
    "en": ("Allow", "Deny"),
    "zh": ("允许", "拒绝"),
}
POLL_TIMEOUT_S = 50
TEXT_LIMIT = 4000  # Telegram allows 4096 per message
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# NanoLika's colour, the sage of nanolika.ru. The Bot API cannot paint a bubble or a line of
# text (the client draws both), so the honest mark of a line written on another device is a
# dot in that colour and a quote block under it, quiet (see :meth:`TelegramChannel.echo`).
LIKA_COLOR = "#52694e"
ECHO_MARK = "🟢"


class TelegramError(RuntimeError):
    pass


class TelegramChannel(Channel):
    name = "telegram"
    label = "Telegram"
    sdk_module = ""
    extra = ""
    supports_edit = True
    console_url = "https://t.me/BotFather"
    fields = (
        Field("bot_token", "Bot token", "secret", required=True, help="From @BotFather, /newbot"),
        Field(
            "proxy", "Proxy", help="Optional, e.g. socks5://127.0.0.1:1080 or http://127.0.0.1:7890"
        ),
    )

    def __init__(self, settings: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(settings)
        self._transport = transport
        self._http: httpx.AsyncClient | None = None
        self._task: asyncio.Task[None] | None = None
        self._offset = 0
        self.topics_enabled = False
        self._bot_id = 0
        self._username = ""
        self._stream_messages: dict[str, int] = {}
        # the relay's linking calls (link_code.py), made on the first code a chat sends
        self._chat_link: ChatLink | None = None

        self._typing: dict[str, asyncio.Task[None]] = {}
        self._approval_cards: dict[tuple[str, str], tuple[str, str, list]] = {}

    # ------------------------------------------------------------------ lifecycle
    def _make_http(self) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {
            "base_url": f"{API}/bot{self.settings.get('bot_token') or ''}",
            "timeout": httpx.Timeout(20.0, read=POLL_TIMEOUT_S + 15),
        }
        proxy = str(self.settings.get("proxy") or "").strip()
        if self._transport is not None:
            kwargs["transport"] = self._transport
        elif proxy:
            kwargs["proxy"] = proxy
        return httpx.AsyncClient(**kwargs)

    async def start(self, bus: ChannelBus) -> None:
        await super().start(bus)
        self._http = self._make_http()
        me = await self._call("getMe")
        self.topics_enabled = bool(me.get("has_topics_enabled"))
        self._bot_id = int(me.get("id") or 0)
        self._username = str(me.get("username") or "")
        # drop whatever queued up while we were away: a reply to a day-old message helps no one
        with suppress(Exception):
            await self._call("deleteWebhook", {"drop_pending_updates": False})
        self._task = asyncio.create_task(self._poll_loop(), name="telegram-poll")
        bus.set_status(self.name, "connected", f"@{self._username}" if self._username else "")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        for typing_task in list(self._typing.values()):
            typing_task.cancel()
        self._typing.clear()
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def verify(self) -> str:
        own = self._http is None
        if own:
            self._http = self._make_http()
        try:
            me = await self._call("getMe")
        finally:
            if own and self._http is not None:
                await self._http.aclose()
                self._http = None
        return f"@{me.get('username') or me.get('first_name') or 'bot'}"

    # ------------------------------------------------------------------ the API
    def _redact(self, text: str) -> str:
        """httpx puts the URL — and so the token — into some of its messages."""
        token = str(self.settings.get("bot_token") or "")
        return text.replace(token, "<token>") if token else text

    async def _call(self, method: str, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        if self._http is None:
            raise TelegramError("Not connected.")
        if params is not None and "chat_id" in params:
            params = dict(params)
            chat, topic = split_chat(str(params["chat_id"]))
            params["chat_id"] = chat
            if topic and (method.startswith("send") or method == "copyMessage"):
                params["message_thread_id"] = topic
        try:
            response = await self._http.post(f"/{method}", json=params, **kwargs)
        except httpx.HTTPError as exc:
            raise TelegramError(self._redact(f"{type(exc).__name__}: {exc}")[:200]) from exc
        data = json_object(response)
        if not data.get("ok"):
            raise TelegramError(str(data.get("description") or f"HTTP {response.status_code}"))
        return data.get("result")

    async def _poll_loop(self) -> None:
        delay = 2.0
        while True:
            try:
                updates = await self._call(
                    "getUpdates",
                    {
                        "offset": self._offset,
                        "timeout": POLL_TIMEOUT_S,
                        "allowed_updates": ["message", "callback_query"],
                    },
                )
                delay = 2.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("telegram: polling failed: {}", self._redact(str(exc)))
                if self.bus is not None:
                    self.bus.set_status(self.name, "connecting", self._redact(str(exc))[:200])
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)
                continue
            if self.bus is not None:
                self.bus.set_status(
                    self.name, "connected", f"@{self._username}" if self._username else ""
                )
            for update in updates or []:
                self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
                try:
                    await self._handle_update(update)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("telegram: update failed: {}", self._redact(str(exc)))

    # ------------------------------------------------------------------ inbound
    async def _handle_update(self, update: dict[str, Any]) -> None:
        if self.bus is None:
            return
        logger.info(
            "telegram: inbound update received ({})",
            "callback"
            if "callback_query" in update
            else "message"
            if "message" in update
            else "other",
        )
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            await self._handle_callback(callback)
            return
        message = update.get("message")
        if not isinstance(message, dict):
            return
        sender = message.get("from") or {}
        if sender.get("is_bot"):
            return
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id") or "")
        is_group = chat.get("type") in ("group", "supergroup")
        text = str(message.get("text") or message.get("caption") or "")
        try:
            from .touches import note as _note

            _note("telegram", sender.get("id"), chat_id, text)
        except Exception:
            pass
        mentioned = not is_group or self._mentioned(message, text)
        text = self._strip_mention(text)
        files: list[InboundFile] = []
        photo = message.get("photo")
        if isinstance(photo, list) and photo:
            best = photo[-1]
            item = await self._download(best.get("file_id"), "photo.jpg", "image")
            if item:
                files.append(item)
        document = message.get("document")
        if isinstance(document, dict):
            item = await self._download(
                document.get("file_id"), str(document.get("file_name") or "file"), "file"
            )
            if item:
                files.append(item)
        voice = message.get("voice") or message.get("audio")
        if isinstance(voice, dict):
            item = await self._download(
                voice.get("file_id"), str(voice.get("file_name") or "voice.ogg"), "audio"
            )
            if item:
                files.append(item)
        topic_event = message.get("forum_topic_created") or message.get("forum_topic_edited")
        if not text and not files and not topic_event:
            return
        if chat.get("type") == "private" and message.get("message_thread_id"):
            chat_id = topic_chat(chat_id, message["message_thread_id"])
        name = " ".join(p for p in (sender.get("first_name"), sender.get("last_name")) if p) or str(
            sender.get("username") or ""
        )
        if not is_group and not files and text and not text.startswith("/"):
            # a chat that has no account yet may send the six characters the app showed
            # (link_code.py); a chat that already belongs to one, or a word that only looks
            # like a code, goes on to the Muse as an ordinary message
            link = self._link()
            answer = await answer_code(
                link,
                str(sender.get("id") or ""),
                text,
                "@" + str(sender["username"]) if sender.get("username") else "",
                getattr(self.bus, "lang", "") or "",
            )
            if answer:
                # A chat that already belongs to an account is a question with two buttons
                # rather than a sentence: the person scanned a code for another account and
                # may well want to leave the old one. `taken` is set by `answer_code`.
                if link.taken and not link.taken.get("own"):
                    menu = getattr(self.bus, "menu", None)
                    if menu is not None:
                        probe = InboundMessage(
                            channel=self.name,
                            chat_id=chat_id,
                            sender_id=str(sender.get("id") or ""),
                            sender_name=name,
                            text="",
                        )
                        await menu.switch_ask(probe, answer, str(link.taken.get("code") or ""))
                        return
                await self.send(chat_id, answer)
                return
        self.bus.inbound(
            InboundMessage(
                channel=self.name,
                chat_id=chat_id,
                sender_id=str(sender.get("id") or ""),
                sender_name=name,
                text=text,
                files=files,
                is_group=is_group,
                mentioned=mentioned,
                message_id=str(message.get("message_id") or ""),
                chat_name=(
                    str((topic_event or {}).get("name") or "")
                    if split_chat(chat_id)[1]
                    else str(chat.get("title") or name)
                ),
            )
        )

    def _link(self) -> ChatLink:
        """The relay's two linking calls, with the settings the sign-in door already uses
        (``cloud_url`` and ``cloud_bot_token``); made once, on the first code. The messenger
        is this bot's own name: the relay keys a chat's account on it, so a Telegram id and
        the same id on MAX stay two people."""
        if self._chat_link is None:
            self._chat_link = ChatLink(
                channel=self.name, settings=self.settings, transport=self._transport
            )
        return self._chat_link

    def chat_link(self) -> ChatLink:
        """The same client, for the manager: `/unlink` is a command rather than a code, so it
        runs in the manager and needs the relay's door that the code path keeps to itself."""
        return self._link()

    def _mentioned(self, message: dict[str, Any], text: str) -> bool:
        reply = message.get("reply_to_message")
        if isinstance(reply, dict) and (reply.get("from") or {}).get("id") == self._bot_id:
            return True
        return bool(self._username) and f"@{self._username}".lower() in text.lower()

    def _strip_mention(self, text: str) -> str:
        if self._username:
            text = text.replace(f"@{self._username}", "")
        return text.strip()

    async def _handle_callback(self, callback: dict[str, Any]) -> None:
        assert self.bus is not None
        raw = str(callback.get("data") or "")
        value: dict[str, Any] = {}
        if raw.startswith("a:") and raw.count(":") >= 2:
            _, decision, approval_id = raw.split(":", 2)
            if decision in {"y", "n", "c", "p", "d"}:
                value = {
                    "approval": approval_id,
                    "decision": "details"
                    if decision == "d"
                    else "deny"
                    if decision == "n"
                    else "allow",
                }
                if decision in {"c", "p"}:
                    value["scope"] = {"c": "conversation", "p": "always"}[decision]
        if raw.startswith("m:") and len(raw.encode()) <= 64:
            value = {"menu": raw[2:]}
        message = callback.get("message") or {}
        chat_id = str((message.get("chat") or {}).get("id") or "")
        if (message.get("chat") or {}).get("type") == "private" and message.get(
            "message_thread_id"
        ):
            chat_id = topic_chat(chat_id, message["message_thread_id"])
        sender_id = str((callback.get("from") or {}).get("id") or "")
        with suppress(Exception):
            await self._call("answerCallbackQuery", {"callback_query_id": callback.get("id")})
        if value:
            if "menu" in value and message.get("message_id"):
                value["_menu_message_id"] = str(message["message_id"])
            self.bus.action(self.name, chat_id, sender_id, value)
            # take the buttons off the card
            if (
                message.get("message_id")
                and "approval" in value
                and value.get("decision") != "details"
            ):
                with suppress(Exception):
                    await self._call(
                        "editMessageReplyMarkup",
                        {
                            "chat_id": chat_id,
                            "message_id": message["message_id"],
                            "reply_markup": {"inline_keyboard": []},
                        },
                    )

    async def _download(self, file_id: Any, name: str, kind: str) -> InboundFile | None:
        if not file_id or self._http is None:
            return None
        try:
            info = await self._call("getFile", {"file_id": file_id})
            path = str(info.get("file_path") or "")
            if not path:
                return None
            token = str(self.settings.get("bot_token") or "")
            response = await self._http.get(f"{API}/file/bot{token}/{path}")
            if response.status_code >= 400:
                return None
            if name in ("photo.jpg", "file") and "." in path.rsplit("/", 1)[-1]:
                name = path.rsplit("/", 1)[-1]
            return InboundFile(name=name, data=response.content, kind=kind)
        except Exception as exc:  # noqa: BLE001
            logger.warning("telegram: download failed: {}", self._redact(str(exc)))
            return None

    # ------------------------------------------------------------------ outbound
    def _lang(self) -> str:
        return str(getattr(self.bus, "lang", "") or "")

    async def _say(self, chat_id: str, part: telegram_format.Part) -> Any:
        """One message of a reply, as HTML; a refused parse sends the same words bare."""
        try:
            return await self._call(
                "sendMessage",
                {"chat_id": chat_id, "text": part.html, "parse_mode": "HTML"},
            )
        except TelegramError as exc:
            if "parse entities" not in str(exc).lower():
                raise
            logger.warning("telegram: HTML refused, sending the words without it")
            text = part.text or telegram_format.plain(part.html)
            return await self._call("sendMessage", {"chat_id": chat_id, "text": text})

    async def _edit(self, chat_id: str, message_id: int, part: telegram_format.Part) -> None:
        try:
            await self._call(
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": part.html,
                    "parse_mode": "HTML",
                },
            )
        except TelegramError as exc:
            if "parse entities" not in str(exc).lower():
                raise
            logger.warning("telegram: HTML refused, sending the words without it")
            text = part.text or telegram_format.plain(part.html)
            await self._call(
                "editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text}
            )

    async def send(self, chat_id: str, text: str, *, files: list[str] | None = None) -> str:
        """A reply from the model: Markdown becomes HTML, and a long answer becomes a few
        messages cut where no tag is left open (telegram_format)."""
        message_id = ""
        for part in telegram_format.parts(text, lang=self._lang()):
            result = await self._say(chat_id, part)
            message_id = str(result.get("message_id") or "")
        for item in files or []:
            path = Path(item)
            if not path.is_file():
                continue
            await self._send_file(chat_id, path)
        return message_id

    async def echo(self, chat_id: str, device: str, text: str, *, silent: bool = True) -> str:
        """A line the person wrote on another device of the account, shown here as what it
        is: their words, not an answer from the Muse.

        The bubble cannot be coloured (the Bot API does not paint one), so the line carries a
        dot in NanoLika's colour and the words go inside a quote block, with the device that
        wrote them naming itself above. ``silent`` keeps the chat from ringing: the message
        is already there the next time it is opened. When the chat is a Telegram Business
        one (``business_connection_id`` set), it is sent through the person's own account.
        """
        body = text.strip()
        if not body:
            return ""
        head = f"{ECHO_MARK} <b>{_html(device)}</b>" if device.strip() else ECHO_MARK
        room = TEXT_LIMIT - len(head) - len("<blockquote>\n</blockquote>")
        if len(body) > room:
            body = body[: max(0, room - 1)] + "…"
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "text": f"{head}\n<blockquote>{_html(body)}</blockquote>",
            "parse_mode": "HTML",
            "disable_notification": bool(silent),
        }
        connection = str(self.settings.get("business_connection_id") or "").strip()
        if connection:
            params["business_connection_id"] = connection
        result = await self._call("sendMessage", params)
        return str(result.get("message_id") or "")

    async def _send_file(self, chat_id: str, path: Path) -> None:
        if self._http is None:
            raise TelegramError("Not connected.")
        is_image = path.suffix.lower() in _IMAGE_SUFFIXES and path.stat().st_size < 10_000_000
        method, field_name = ("sendPhoto", "photo") if is_image else ("sendDocument", "document")
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        physical, topic = split_chat(chat_id)
        destination: dict[str, Any] = {"chat_id": physical}
        if topic:
            destination["message_thread_id"] = topic
        with path.open("rb") as fh:
            response = await self._http.post(
                f"/{method}", data=destination, files={field_name: (path.name, fh, mime)}
            )
        data = json_object(response)
        if not data.get("ok"):
            raise TelegramError(str(data.get("description") or f"HTTP {response.status_code}"))

    async def stream(self, chat_id: str, stream_id: str, text: str, *, done: bool) -> None:
        """The reply as it is written: the first message is edited in place, and whatever
        did not fit is sent once the answer is complete."""
        parts = telegram_format.parts(text, lang=self._lang())
        wait = telegram_format.Part(html="…", text="…")
        shown = parts[0] if parts else wait
        message_id = self._stream_messages.get(stream_id)
        if message_id is None:
            result = await self._say(chat_id, shown)
            self._stream_messages[stream_id] = int(result.get("message_id") or 0)
        else:
            try:
                await self._edit(chat_id, message_id, shown)
            except TelegramError as exc:
                if "not modified" not in str(exc):
                    raise
        if done:
            self._stream_messages.pop(stream_id, None)
            for part in parts[1:]:
                await self._say(chat_id, part)

    async def ask(self, chat_id: str, approval_id: str, text: str, lang: str = "") -> None:
        await self.ask_scoped(chat_id, approval_id, text, lang, ["once"])

    async def ask_scoped(
        self, chat_id: str, approval_id: str, text: str, lang: str, scopes: list[str]
    ) -> None:
        rows = []
        for code, label, _scope in choices(scopes, lang):
            data = f"a:{code}:{approval_id}"
            if len(data.encode("utf-8")) <= 64:
                rows.append([{"text": label, "callback_data": data}])
        rows = [rows[0] + rows[-1]] + rows[1:-1]
        label = {"ru": "Подробности", "zh": "详情"}.get(lang, "Details")
        detail_data = f"a:d:{approval_id}"
        if len(detail_data.encode("utf-8")) <= 64:
            rows.append([{"text": label, "callback_data": detail_data}])
        result = await self._call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text[:TEXT_LIMIT],
                "reply_markup": {"inline_keyboard": rows},
            },
        )

        self._approval_cards[(chat_id, approval_id)] = (
            str(result.get("message_id") or ""),
            text,
            rows,
        )
        while len(self._approval_cards) > 256:
            self._approval_cards.pop(next(iter(self._approval_cards)))

    async def expand_approval(
        self, chat_id: str, approval_id: str, details: str, lang: str, scopes: list[str]
    ) -> None:
        card = self._approval_cards.get((chat_id, approval_id))
        if card is None or not card[0]:
            # A recoverable fallback still keeps decision buttons below the details.
            await self.ask_scoped(chat_id, approval_id, details, lang, scopes)
            return
        message_id, original, rows = card
        prompt = {
            "ru": "Решение ещё не принято. Выберите действие ниже.",
            "zh": "尚未授权，请在下方选择。",
        }.get(lang, "No decision yet. Choose an action below.")
        body = original[:1100] + "\n\n" + details[:2600] + "\n\n" + prompt
        decision_rows = rows[:-1]
        try:
            await self._call(
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": body,
                    "reply_markup": {"inline_keyboard": decision_rows},
                },
            )
        except TelegramError as exc:
            if "not modified" not in str(exc):
                raise

    async def links(self, chat_id: str, buttons: list[tuple[str, str]], text: str = "") -> None:
        rows = [
            [{"text": label[:64], "url": url}]
            for label, url in buttons
            if url.startswith("https://") or url.startswith("http://")
        ]
        if not rows:
            return
        await self._call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text or "…",
                "reply_markup": {"inline_keyboard": rows},
                "disable_web_page_preview": True,
            },
        )

    async def typing(self, chat_id: str, on: bool) -> None:
        current = self._typing.pop(chat_id, None)
        if current is not None:
            current.cancel()
        if on:
            self._typing[chat_id] = asyncio.create_task(self._typing_loop(chat_id))

    async def _typing_loop(self, chat_id: str) -> None:
        # Telegram shows "typing…" for ~5 s per call
        for _ in range(60):
            with suppress(Exception):
                await self._call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
            await asyncio.sleep(4.5)


def _html(text: str) -> str:
    """What the Bot API's HTML parse mode lets through: everything else is escaped, so a
    person's own words never break a message (``<`` in code, a stray ``&``)."""
    return escape(text, quote=False)


__all__ = ["TelegramChannel", "TelegramError"]
