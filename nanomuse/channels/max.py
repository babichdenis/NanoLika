"""MAX Bot API over a verified HTTPS webhook, using the normal channel pairing gate."""

from __future__ import annotations

import asyncio
import hmac
import json
import mimetypes
import re
import ssl
from collections import deque
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, Request

from nanomuse.channels.approval_choices import choices
from nanomuse.channels.base import (
    Channel,
    ChannelBus,
    Field,
    InboundFile,
    InboundMessage,
    json_object,
)
from nanomuse.channels.link_code import ChatLink, answer_code, as_code
from nanomuse.logger import logger

API = "https://platform-api2.max.ru"
MAX_BODY_BYTES = 256 * 1024
# MAX's own ceilings: an image is 50 MB, a video 250 MB, audio 256 MB, other files 4 GB.
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".tiff", ".bmp", ".heic"}
_VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm"}
_AUDIO_SUFFIXES = {".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".flac"}
_IMAGE_MAX = 50 * 1024 * 1024
_VIDEO_MAX = 250 * 1024 * 1024
_AUDIO_MAX = 256 * 1024 * 1024


def _attachment_type(path: Path) -> str:
    """What MAX calls this file: an image, a video, audio, or just a file.

    A file over a media type's ceiling is sent as a file instead, which MAX accepts up
    to 4 GB; it will not play inline, but it arrives.
    """
    suffix = path.suffix.lower()
    size = path.stat().st_size
    if suffix in _IMAGE_SUFFIXES and size <= _IMAGE_MAX:
        return "image"
    if suffix in _VIDEO_SUFFIXES and size <= _VIDEO_MAX:
        return "video"
    if suffix in _AUDIO_SUFFIXES and size <= _AUDIO_MAX:
        return "audio"
    return "file"


def _uploaded_token(data: dict[str, Any]) -> str:
    """The token an upload answered with: top-level, or under `photos` for an image."""
    token = data.get("token")
    if isinstance(token, str) and token:
        return token
    photos = data.get("photos")
    if isinstance(photos, dict):
        for group in photos.values():
            if not isinstance(group, dict):
                continue
            token = group.get("token")
            if isinstance(token, str) and token:
                return token
            ids = group.get("photoIds")
            if isinstance(ids, dict) and isinstance(ids.get("token"), str):
                return ids["token"]
    return ""


class MaxError(RuntimeError):
    pass


class MaxChannel(Channel):
    name = "max"
    label = "MAX"
    sdk_module = ""
    extra = ""
    supports_edit = True
    console_url = "https://business.max.ru/"
    fields = (
        Field("bot_token", "Bot token", "secret", required=True),
        Field("webhook_url", "HTTPS webhook URL", required=True),
        Field("webhook_secret", "Webhook secret", "secret", required=True),
        Field("ca_file", "Additional CA certificate file"),
    )

    def __init__(self, settings: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(settings)
        self._transport = transport
        self._http: httpx.AsyncClient | None = None
        # Media goes to signed CDN/upload hosts: a client without the bot token keeps
        # the credential out of those requests.
        self._media_http: httpx.AsyncClient | None = None
        self._seen: set[str] = set()
        self._order: deque[str] = deque()
        self._streams: dict[str, str] = {}
        self._typing: dict[str, asyncio.Task[None]] = {}
        self._approval_cards: dict[tuple[str, str], tuple[str, str, list]] = {}
        # the relay's linking calls (link_code.py), made on the first code a chat sends
        self._chat_link: ChatLink | None = None
        self._link_tasks: set[asyncio.Task[Any]] = set()
        self._media_tasks: set[asyncio.Task[Any]] = set()

    def _ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        ca = str(
            self.settings.get("ca_file")
            or Path(__file__).with_name("certificates") / "russian-root.pem"
        ).strip()
        if ca:
            ctx.load_verify_locations(ca)
        return ctx

    def _make_http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=API,
            headers={"Authorization": str(self.settings.get("bot_token") or "")},
            verify=self._ssl_context(),
            timeout=20,
            transport=self._transport,
            follow_redirects=False,
        )

    def _make_media_http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            verify=self._ssl_context(),
            timeout=300,
            transport=self._transport,
            follow_redirects=False,
        )

    async def _call(self, method: str, path: str, **kwargs: Any) -> dict:
        if self._http is None:
            raise MaxError("MAX is not connected")
        try:
            r = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError:
            raise MaxError("MAX connection failed") from None
        data = json_object(r)
        if r.status_code >= 400 or data.get("success") is False:
            # Never include headers, arbitrary provider bodies, tokens or user content.
            raise MaxError(f"MAX request failed (HTTP {r.status_code})")
        return data

    async def verify(self) -> str:
        own = self._http is None
        if own:
            self._http = self._make_http()
        try:
            me = await self._call("GET", "/me")
            if not me.get("is_bot"):
                raise MaxError("MAX credentials do not identify a bot")
            return str(me.get("username") or "MAX bot")
        finally:
            if own and self._http is not None:
                await self._http.aclose()
                self._http = None

    async def start(self, bus: ChannelBus) -> None:
        url = str(self.settings.get("webhook_url") or "")
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise MaxError("MAX requires a public HTTPS webhook URL without credentials or query")
        secret = str(self.settings.get("webhook_secret") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", secret):
            raise MaxError("MAX webhook secret must contain 32 to 256 URL-safe characters")
        await super().start(bus)
        self._http = self._make_http()
        self._media_http = self._make_media_http()
        try:
            name = await self.verify()
            subscriptions = await self._call("GET", "/subscriptions")
            if any(s.get("url") != url for s in subscriptions.get("subscriptions", [])):
                raise MaxError("MAX has another webhook; review it before replacing it")
            await self._call(
                "POST",
                "/subscriptions",
                json={
                    "url": url,
                    "secret": secret,
                    "update_types": ["message_created", "message_callback", "bot_started"],
                },
            )
            bus.set_status(self.name, "connected", name)
        except Exception:
            await self.stop()
            raise

    async def stop(self) -> None:
        tasks = list(self._typing.values())
        self._typing.clear()
        # a code still being handed to the relay would answer into a closed connection
        tasks += list(self._link_tasks)
        self._link_tasks.clear()
        tasks += list(self._media_tasks)
        self._media_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._media_http is not None:
            await self._media_http.aclose()
            self._media_http = None
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        self._streams.clear()

    def _duplicate(self, key: str) -> bool:
        if key in self._seen:
            return True
        self._seen.add(key)
        self._order.append(key)
        while len(self._order) > 4096:
            self._seen.discard(self._order.popleft())
        return False

    def handle_update(self, data: dict) -> None:
        bus = self.bus
        if bus is None:
            return
        kind = data.get("update_type")
        logger.info("max: inbound update received ({})", kind or "unknown")
        if kind == "bot_started":
            user = data.get("user") or {}
            chat = str(data.get("chat_id") or "")
            sender = str(user.get("user_id") or "")
            # The deep link's payload is a one-use secret: the log says whether MAX passed
            # one, never what it was. A missing payload here is why a QR looks scanned and
            # connects nothing, so the line earns its noise.
            logger.info(
                "max: bot started by {} (payload {})",
                sender or "unknown",
                "present" if str(data.get("payload") or "").lower().startswith("nl_") else "absent",
            )
            key = f"start:{chat}:{sender}:{data.get('timestamp')}"
            if chat and sender and not self._duplicate(key):
                bus.inbound(
                    InboundMessage(
                        channel=self.name,
                        chat_id=chat,
                        sender_id=sender,
                        text="/start " + str(data.get("payload") or "")
                        if str(data.get("payload") or "").lower().startswith("nl_")
                        else "/start",
                    )
                )
            return
        message = data.get("message") or {}
        recipient = message.get("recipient") or {}
        # Private, explicitly paired chats only in this first integration.
        if recipient.get("chat_type") != "dialog":
            return
        chat = str(recipient.get("chat_id") or "")
        body = message.get("body") or {}
        if kind == "message_callback":
            callback = data.get("callback") or {}
            sender = str((callback.get("user") or {}).get("user_id") or "")
            try:
                value = json.loads(callback.get("payload") or "{}")
            except (ValueError, TypeError):
                return
            key = str(callback.get("callback_id") or "")
            if (
                chat
                and sender
                and key
                and isinstance(value, dict)
                and not self._duplicate("callback:" + key)
            ):
                if "menu" in value:
                    value["_menu_message_id"] = str(body.get("mid") or "")
                bus.action(self.name, chat, sender, value)
            return
        if kind != "message_created":
            return
        user = message.get("sender") or {}
        if user.get("is_bot"):
            return
        sender = str(user.get("user_id") or "")
        mid = str(body.get("mid") or "")
        text = str(body.get("text") or "").strip()
        if not chat or not sender or not mid or self._duplicate("message:" + mid):
            return
        attachments = body.get("attachments")
        if isinstance(attachments, list) and attachments:
            # The webhook must answer before a clip is downloaded; the file join the
            # message when the bytes are here. A test without a loop takes the text only.
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                task = loop.create_task(self._deliver_media(chat, sender, user, body, text, mid))
                self._media_tasks.add(task)
                task.add_done_callback(self._media_tasks.discard)
                return
        self._deliver(chat, sender, user, text, [], mid)

    async def _deliver_media(
        self,
        chat: str,
        sender: str,
        user: dict[str, Any],
        body: dict[str, Any],
        text: str,
        mid: str,
    ) -> None:
        files = await self._attachments(body)
        if text or files:
            self._deliver(chat, sender, user, text, files, mid)

    def _deliver(
        self,
        chat: str,
        sender: str,
        user: dict[str, Any],
        text: str,
        files: list[InboundFile],
        mid: str,
    ) -> None:
        try:
            from .touches import note as _note

            _note("max", sender, chat, text)
        except Exception:
            pass
        inbound = InboundMessage(
            channel=self.name,
            chat_id=chat,
            sender_id=sender,
            sender_name=str(user.get("name") or ""),
            text=text,
            files=files,
            message_id=mid,
        )
        if self._link_first(inbound):
            return
        if self.bus is not None:
            self.bus.inbound(inbound)

    async def _attachments(self, body: dict[str, Any]) -> list[InboundFile]:
        """The pictures, clips, voice and documents of one message, fetched into memory.

        MAX gives an image, an audio file and a document a direct `url`; a video's
        `url` is a page, so its token is exchanged for the mp4 links with
        `GET /videos/{token}`. A file that cannot be read is left out, not fatal:
        the words of the message still reach the agent.
        """
        out: list[InboundFile] = []
        items = body.get("attachments")
        if not isinstance(items, list):
            return out
        for item in items:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("type") or "")
            raw_payload = item.get("payload")
            payload = raw_payload if isinstance(raw_payload, dict) else {}
            if kind == "image":
                url = str(payload.get("url") or "")
                name = f"photo-{payload.get('photo_id') or 'max'}.jpg"
                got = await self._download_url(url, name, "image")
            elif kind == "video":
                token = str(payload.get("token") or "")
                url = str(payload.get("url") or "")
                if not url and token:
                    details = await self._call("GET", f"/videos/{token}")
                    raw_urls = details.get("urls")
                    urls = raw_urls if isinstance(raw_urls, dict) else {}
                    for key in ("mp4_1080", "mp4_720", "mp4_480", "mp4_360", "mp4_240"):
                        candidate = urls.get(key)
                        if isinstance(candidate, str) and candidate:
                            url = candidate
                            break
                name = f"video-{token or 'max'}.mp4"
                got = await self._download_url(url, name, "video")
            elif kind == "audio":
                url = str(payload.get("url") or "")
                token = str(payload.get("token") or "max")
                name = f"audio-{token}.mp3"
                got = await self._download_url(url, name, "audio")
            elif kind == "file":
                url = str(payload.get("url") or "")
                name = str(item.get("filename") or "file")
                got = await self._download_url(url, name, "file")
            else:
                continue
            if got is not None:
                out.append(got)
        return out

    async def _download_url(self, url: str, name: str, kind: str) -> InboundFile | None:
        if not url or self._media_http is None:
            return None
        try:
            response = await self._media_http.get(url, timeout=120)
        except httpx.HTTPError:
            return None
        if response.status_code >= 400 or not response.content:
            return None
        return InboundFile(name=name, data=response.content, kind=kind)

    def _link(self) -> ChatLink:
        """The relay's two linking calls, with the settings the sign-in door already uses
        (``cloud_url`` and ``cloud_bot_token``); made once, on the first code. The messenger
        is this bot's own name, and it is what keeps a MAX id apart from the Telegram id with
        the same digits: the relay keys a chat's account on the pair.

        The relay and its shared bot secret are account-wide, but older configs kept them
        only in ``[channels.telegram]``. A MAX channel without its own pair borrows the
        Telegram one: a QR that opens the bot must not end in "connect later" because the
        second messenger was never given the relay's address."""
        if self._chat_link is None:
            settings = dict(self.settings)
            store = getattr(getattr(self, "bus", None), "store", None)
            if store is not None:
                shared = store.resolved(store.config("telegram"))
                for key in ("cloud_url", "cloud_bot_token"):
                    if not str(settings.get(key) or "").strip() and shared.get(key):
                        settings[key] = shared[key]
            self._chat_link = ChatLink(
                channel=self.name, settings=settings, transport=self._transport
            )
        return self._chat_link

    def chat_link(self) -> ChatLink:
        """The same client, for the manager: `/unlink` is a command rather than a code, so it
        runs in the manager and needs the relay's door that the code path keeps to itself."""
        return self._link()

    def _link_first(self, msg: InboundMessage) -> bool:
        """A chat with no account yet may send the six characters the app showed.

        True when the message was taken for that: the answer comes from the relay's call,
        which is not quick enough to hold the webhook, so `_answer_code` runs on its own
        and hands the message on when it turns out not to be a code after all."""
        if not as_code(msg.text) or not self._link().configured():
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no loop: the webhook path always has one, a test may not
            return False
        task = loop.create_task(self._answer_code(msg))
        self._link_tasks.add(task)
        task.add_done_callback(self._link_tasks.discard)
        return True

    async def _answer_code(self, msg: InboundMessage) -> None:
        bus = self.bus
        if bus is None:
            return
        try:
            link = self._link()
            answer = await answer_code(
                link, msg.sender_id, msg.text, "", getattr(bus, "lang", "") or ""
            )
        except Exception as exc:  # noqa: BLE001 — a code must never break the chat
            logger.warning("max: link code failed: {}", type(exc).__name__)
            answer = ""
            link = None
        if not answer:
            bus.inbound(msg)
            return
        # A scanned code for a chat that already belongs to an account: ask, in two buttons,
        # whether to leave that account. The same rule as Telegram, so the two bots behave alike.
        if link is not None and link.taken and not link.taken.get("own"):
            menu = getattr(bus, "menu", None)
            if menu is not None:
                await menu.switch_ask(msg, answer, str(link.taken.get("code") or ""))
                return
        with suppress(MaxError):
            await self.send(msg.chat_id, answer)

    async def typing(self, chat_id: str, on: bool) -> None:
        current = self._typing.get(chat_id)
        if on and current is not None and not current.done():
            return
        current = self._typing.pop(chat_id, None)
        if current is not None:
            current.cancel()
            await asyncio.gather(current, return_exceptions=True)
        if on:
            self._typing[chat_id] = asyncio.create_task(self._typing_loop(chat_id))

    async def _typing_loop(self, chat_id: str) -> None:
        # Activity expires on MAX; refresh only while the manager reports working.
        while True:
            with suppress(MaxError):
                await self._call("POST", f"/chats/{chat_id}/actions", json={"action": "typing_on"})
            await asyncio.sleep(4.5)

    async def send(self, chat_id: str, text: str, *, files: list[str] | None = None) -> str:
        last = ""
        for start in range(0, len(text), 4000):
            result = await self._call(
                "POST",
                "/messages",
                params={"chat_id": chat_id},
                json={"text": text[start : start + 4000]},
            )
            last = str(((result.get("message") or {}).get("body") or {}).get("mid") or "")
        for item in files or []:
            path = Path(item)
            if not path.is_file():
                continue
            last = await self._send_file(chat_id, path) or last
        return last

    async def _send_file(self, chat_id: str, path: Path) -> str:
        """Upload one file and send it as an attachment of its own message.

        MAX takes a token per file (`POST /uploads`), then `POST /messages` with that
        token; a file cannot share a message with an image or a video, so each goes
        alone. A just-uploaded file is sometimes not processed yet: the answer is
        `attachment.not.ready`, and the same token is retried with a growing pause.
        """
        if self._http is None or self._media_http is None:
            raise MaxError("MAX is not connected")
        kind = _attachment_type(path)
        upload = await self._call("POST", "/uploads", params={"type": kind})
        url = str(upload.get("url") or "")
        if not url:
            raise MaxError("MAX did not return an upload URL")
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            with path.open("rb") as fh:
                response = await self._media_http.post(
                    url, files={"data": (path.name, fh, mime)}, timeout=300
                )
            if response.status_code in (401, 403):
                # MAX's image upload has been seen to ask for the token; the media
                # client deliberately does not carry it, so retry with it only now.
                with path.open("rb") as fh:
                    response = await self._http.post(
                        url, files={"data": (path.name, fh, mime)}, timeout=300
                    )
        except httpx.HTTPError:
            raise MaxError("MAX upload failed") from None
        data = json_object(response)
        if response.status_code >= 400:
            raise MaxError(f"MAX upload failed (HTTP {response.status_code})")
        token = str(upload.get("token") or "") or _uploaded_token(data)
        if not token:
            raise MaxError("MAX did not return an attachment token")
        attachment = {"type": kind, "payload": {"token": token}}
        for delay in (0.0, 1.0, 2.0, 4.0):
            if delay:
                await asyncio.sleep(delay)
            try:
                response = await self._http.post(
                    "/messages", params={"chat_id": chat_id}, json={"attachments": [attachment]}
                )
            except httpx.HTTPError:
                raise MaxError("MAX connection failed") from None
            data = json_object(response)
            if response.status_code < 400:
                return str(((data.get("message") or {}).get("body") or {}).get("mid") or "")
            if str(data.get("code") or "") != "attachment.not.ready":
                raise MaxError(f"MAX request failed (HTTP {response.status_code})")
        raise MaxError("MAX attachment was not ready")

    async def stream(self, chat_id: str, stream_id: str, text: str, *, done: bool) -> None:
        if not text.strip():
            return
        if len(text) > 4000:
            if done:
                if stream_id in self._streams:
                    await self._call(
                        "PUT",
                        "/messages",
                        params={"message_id": self._streams.pop(stream_id)},
                        json={"text": text[:4000]},
                    )
                    await self.send(chat_id, text[4000:])
                else:
                    await self.send(chat_id, text)
            return
        mid = self._streams.get(stream_id)
        if mid:
            await self._call("PUT", "/messages", params={"message_id": mid}, json={"text": text})
        else:
            mid = await self.send(chat_id, text)
            if mid:
                self._streams[stream_id] = mid
        if done:
            self._streams.pop(stream_id, None)

    async def ask(self, chat_id: str, approval_id: str, text: str, lang: str = "") -> None:
        await self.ask_scoped(chat_id, approval_id, text, lang, ["once"])

    async def ask_scoped(
        self, chat_id: str, approval_id: str, text: str, lang: str, scopes: list[str]
    ) -> None:
        rows = [
            [
                {
                    "type": "callback",
                    "text": label,
                    "payload": json.dumps(
                        {
                            "approval": approval_id,
                            "decision": "deny" if code == "n" else "allow",
                            "scope": scope,
                        }
                    ),
                }
            ]
            for code, label, scope in choices(scopes, lang)
        ]
        rows = [rows[0] + rows[-1]] + rows[1:-1]
        rows.append(
            [
                {
                    "type": "callback",
                    "text": {"ru": "Подробности", "zh": "详情"}.get(lang, "Details"),
                    "payload": json.dumps({"approval": approval_id, "decision": "details"}),
                }
            ]
        )
        result = await self._call(
            "POST",
            "/messages",
            params={"chat_id": chat_id},
            json={
                "text": text[:4000],
                "attachments": [{"type": "inline_keyboard", "payload": {"buttons": rows}}],
            },
        )

        self._approval_cards[(chat_id, approval_id)] = (
            str(((result.get("message") or {}).get("body") or {}).get("mid") or ""),
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
        await self._call(
            "PUT",
            "/messages",
            params={"message_id": message_id},
            json={
                "text": body,
                "attachments": [{"type": "inline_keyboard", "payload": {"buttons": decision_rows}}],
            },
        )


async def receive_webhook(channel: MaxChannel | None, request: Request) -> dict:
    if channel is None or channel._http is None or channel.bus is None:
        raise HTTPException(503, "MAX is not configured")
    expected = str(channel.settings.get("webhook_secret") or "")
    supplied = request.headers.get("X-Max-Bot-Api-Secret", "")
    if not expected or not hmac.compare_digest(expected.encode(), supplied.encode()):
        raise HTTPException(403, "Webhook authentication failed")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_BODY_BYTES:
            raise HTTPException(413, "Webhook too large")
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError
        # Invalid nested shapes must never reach the model or its tools.
        if data.get("update_type") == "message_callback":
            callback = data.get("callback") or {}
            key = str(callback.get("callback_id") or "")
            if key:
                with suppress(Exception):
                    await channel._call("POST", "/answers", params={"callback_id": key}, json={})
        channel.handle_update(data)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(400, "Malformed MAX update") from None
    return {"ok": True}
