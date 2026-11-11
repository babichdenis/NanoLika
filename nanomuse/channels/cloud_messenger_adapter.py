"""Wire the account-scoped messenger bridge to Telegram/MAX and the relay.

The bridge is deliberately opt-in until the relay coordinates delivery with every
other sender and supports attachments; it must not silently replace the existing bot.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import secrets
import tempfile
from pathlib import Path
from typing import Any

import httpx

from nanomuse.channels import telegram_format
from nanomuse.channels.account_picker import CatalogUnavailable, ConversationUnavailable
from nanomuse.channels.account_topics import AccountTopics
from nanomuse.channels.base import InboundMessage
from nanomuse.channels.cloud_delivery import DeliveryConflict, DeliveryDeferred, DeliveryFull
from nanomuse.channels.cloud_messenger import (
    CloudMessengerBridge,
    DeliveryJournalFull,
    JobConflict,
    StorageFull,
)
from nanomuse.channels.telegram_topics import split_chat, topic_chat
from nanomuse.logger import logger
from nanomuse.messenger_attachments import AttachmentError, parse_files, wire_files


class BridgeUnavailable(Exception):
    pass


class CloudMessengerAdapter:
    def __init__(self, manager: Any):
        self.m = manager
        root = Path(manager.svc.data_dir) / "channels"
        self.bridge = CloudMessengerBridge(root)
        self.topics = AccountTopics(root / "cloud-topics.sqlite3", self.bridge.picker)
        self._menus: dict[tuple[str, str, str, str], list[str]] = {}
        self._recovery_menus: dict[tuple[str, str, str, str], list[dict[str, str]]] = {}
        self._running: set[str] = set()
        self._capacity = asyncio.Semaphore(20)

    async def _identity(self, msg: InboundMessage) -> tuple[str, str, str, str]:
        key, _model = await self.m._cloud_key_for(msg.channel, msg)
        scope = self.m._cloud_customer_scopes.get((msg.channel, msg.sender_id), "")
        held = self.m._cloud_keys.get((msg.channel, msg.sender_id))
        settings = self.m.store.resolved(self.m.store.config(msg.channel))
        token = str(settings.get("cloud_bot_token") or "")
        if (
            not key or key == "!" or not scope.startswith("account:") or scope == "account:"
            or not held or len(held) < 5 or held[0] != key or not held[4] or not token
        ):
            raise BridgeUnavailable("account unavailable")
        return scope[len("account:"):], key, str(held[4]), token

    async def _catalog(self, account: str, key: str, base: str, msg: InboundMessage) -> dict[str, Any]:
        reader = "messenger-" + hashlib.sha256(f"{msg.channel}\n{msg.chat_id}".encode()).hexdigest()[:32]
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
                    base + "/v1/sync/catalog", params={"client": reader},
                    headers={"Authorization": "Bearer " + key},
                )
            if response.status_code != 200:
                raise BridgeUnavailable("catalog unavailable")
            data = response.json()
            self.bridge.picker._active(account, data)
            return data
        except (httpx.HTTPError, ValueError, CatalogUnavailable) as exc:
            raise BridgeUnavailable("catalog unavailable") from exc

    async def _sync_topics(self, account: str, msg: InboundMessage, catalog: dict[str, Any]) -> None:
        channel = self.m.channels.get("telegram")
        if msg.channel != "telegram" or channel is None or not getattr(channel, "topics_enabled", False):
            return
        chat, _topic = split_chat(msg.chat_id)
        if chat != msg.sender_id:
            return  # Native private topics only; never publish a person's chats into a group.
        started = self.topics.enroll(account, msg.sender_id, chat)
        active = self.bridge.picker._active(account, catalog)
        for row in self.m.picker.topics.file.load().values():
            if not isinstance(row, dict) or row.get("account") != account or row.get("owner") != msg.sender_id or row.get("chat") != chat:
                continue
            cid, topic = str(row.get("cid") or ""), int(row.get("topic") or 0)
            if cid and topic > 0:
                self.topics.register(account, msg.sender_id, chat, cid, topic, catalog, retired=bool(row.get("archived") or cid not in active))
        known = {row["cid"] for row in self.topics.existing(account, msg.sender_id, chat)}
        # Existing unmapped topics cannot be listed through Telegram's bot API. Only
        # auto-create conversations born after enrollment; import known old mappings.
        visible = []
        for row in catalog["conversations"]:
            if not isinstance(row, dict):
                continue
            try:
                created = float(row.get("created_at") or 0)
            except (TypeError, ValueError):
                created = 0
            if str(row.get("cid") or "") in known or created > started:
                visible.append(row)

        async def create(name: str) -> int:
            result = await channel._call("createForumTopic", {"chat_id": chat, "name": name})
            return int(result.get("message_thread_id") or 0)

        await self.topics.reconcile(account, msg.sender_id, chat, {**catalog, "conversations": visible}, create, topic_chat)

    async def _worker(self, job: dict[str, str], suffix: str) -> dict[str, Any]:
        msg = InboundMessage(channel=job["channel"], chat_id=job["chat"], sender_id=job["sender"])
        account, key, base, token = await self._identity(msg)
        if account != job["account"]:
            raise BridgeUnavailable("account changed")
        body = {
            "channel": job["channel"], "sender_id": job["sender"], "operation_id": job["operation"],
            "conversation_id": job["conversation"], "text": job["text"],
        }
        if suffix == "message":
            body["files"] = self.bridge.attachments(job["operation"], account)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    base + "/v1/bot/runtime/" + suffix,
                    headers={"X-Bot-Token": token, "Authorization": "Bearer " + key}, json=body,
                )
            if response.status_code == 409:
                return {"state": "unknown"}
            if response.status_code != 200:
                raise BridgeUnavailable("worker unavailable")
            data = response.json()
            return data if isinstance(data, dict) else {"state": "unknown"}
        except (httpx.HTTPError, ValueError) as exc:
            raise BridgeUnavailable("worker unavailable") from exc

    @staticmethod
    def _parts(text: str) -> list[str]:
        return [json.dumps([part.html, part.text], ensure_ascii=False) for part in telegram_format.parts(text, limit=3500)]

    async def _relay_delivery(self, job: dict[str, str], action: str, body: dict[str, Any]) -> dict[str, Any]:
        msg = InboundMessage(channel=job["channel"], chat_id=job["chat"], sender_id=job["sender"])
        account, key, base, _token = await self._identity(msg)
        if account != job["account"]:
            raise BridgeUnavailable("account changed")
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    base + "/v1/delivery/" + action,
                    headers={"Authorization": "Bearer " + key}, json=body,
                )
            if response.status_code != 200:
                try:
                    error = response.json().get("error", {})
                    code = str(error.get("code") or "") if isinstance(error, dict) else ""
                except (ValueError, AttributeError):
                    code = ""
                if code in {"delivery_journal_full", "delivery_decisions_full"}:
                    raise DeliveryJournalFull("delivery journal is full")
            if response.status_code == 409:
                raise DeliveryConflict("delivery may already exist on another device")
            if response.status_code != 200:
                raise BridgeUnavailable("delivery coordination unavailable")
            result = response.json()
            if not isinstance(result, dict):
                raise BridgeUnavailable("delivery coordination unavailable")
            return result
        except (httpx.HTTPError, ValueError) as exc:
            raise BridgeUnavailable("delivery coordination unavailable") from exc

    @staticmethod
    def _delivery_key(job: dict[str, str]) -> dict[str, str]:
        return {"mid": job["response_mid"], "channel": job["channel"], "chat": job["chat"]}

    async def _prepare_delivery(self, job: dict[str, str], chunks: Any) -> dict[str, Any]:
        body = {**self._delivery_key(job), "chunks": [hashlib.sha256(chunk.encode()).hexdigest() for chunk in chunks]}
        return await self._relay_delivery(job, "prepare", body)

    async def _send_part(self, job: dict[str, str], index: int, chunk: str) -> str:
        claim = secrets.token_urlsafe(32)
        body = {**self._delivery_key(job), "part": index, "claim": claim}
        response = await self._relay_delivery(job, "claim", body)
        state = response.get("state")
        if state == "sent" and response.get("remote_id"):
            return str(response["remote_id"])
        if state in {"sending", "blocked"}:
            raise DeliveryDeferred("another sender is handling this part")
        if state != "claimed":
            raise BridgeUnavailable("delivery outcome is unknown")
        try:
            remote_id = await self._platform_part(job, index, chunk)
            if not remote_id:
                raise BridgeUnavailable("platform did not confirm delivery")
        except BaseException:
            try:
                await asyncio.shield(self._relay_delivery(job, "finish", {**body, "state": "unknown"}))
            except BaseException:
                pass  # sending remains reserved and cannot be automatically repeated
            raise
        result = await self._relay_delivery(job, "finish", {**body, "state": "sent", "remote_id": remote_id})
        if result.get("state") != "sent":
            raise BridgeUnavailable("delivery confirmation is unknown")
        return remote_id

    async def _platform_part(self, job: dict[str, str], index: int, chunk: str) -> str:
        # Each invocation is exactly one request. Channel.send() may split again and
        # ChannelManager._send() swallows errors; neither is suitable for an outbox.
        channel = self.m.channels.get(job["channel"])
        if channel is None:
            raise BridgeUnavailable("channel unavailable")
        value = json.loads(chunk)
        if isinstance(value, dict) and value.get("type") == "file":
            item = {key: value[key] for key in ("name", "kind", "sha256", "data") if key in value}
            parsed = parse_files([item])[0]
            # A separate temporary directory preserves the original name and is removed
            # even if the messenger times out or cancels delivery.
            with tempfile.TemporaryDirectory(prefix="nanolika-delivery-") as directory:
                path = Path(directory) / parsed["name"]
                path.write_bytes(parsed["data"])
                path.chmod(0o600)
                return str(await channel._send_file(job["chat"], path) or "")
        part = telegram_format.Part(*value)
        reply = job["message"] if index == 0 and job["message"].isdigit() else ""
        if job["channel"] == "telegram":
            result = await channel._say(job["chat"], part, reply)
            return str(result.get("message_id") or "")
        return str(await channel._send_part(job["chat"], part, reply) or "")

    async def _advance(self, job: dict[str, str]) -> str:
        msg = InboundMessage(channel=job["channel"], chat_id=job["chat"], sender_id=job["sender"])
        account, key, base, _token = await self._identity(msg)
        if account != job["account"]:
            raise BridgeUnavailable("account changed")
        catalog = await self._catalog(account, key, base, msg)
        return await self.bridge.advance(
            job, catalog, lambda row: self._worker(row, "message"), lambda row: self._worker(row, "status"),
            self._parts, self._send_part, prepare=self._prepare_delivery,
        )

    async def resume(self) -> None:
        """Recheck persisted jobs; never resend an ambiguous platform message."""
        while self.m.started:
            self._schedule_due()
            await asyncio.sleep(5)

    def _schedule_due(self) -> None:
        capacity = max(0, 20 - len(self._running))
        for job in self.bridge.claim_due(capacity):
            if job["operation"] in self._running:
                continue
            self._running.add(job["operation"])
            self.m._spawn(self._resume_one(job))

    async def _resume_one(self, job: dict[str, str]) -> None:
        failed = False
        try:
            async with self._capacity:
                state = await self._advance(job)
                if state == "delivery_full" and self.bridge.claim_notice(job["operation"], "delivery_full"):
                    await self.m._send(job["channel"], job["chat"], "История доставок заполнена. Обратитесь в поддержку; автоматически повторять отправку не буду.")
                if state == "rejected" and self.bridge.claim_notice(job["operation"], "rejected"):
                    await self.m._send(job["channel"], job["chat"], "Эта задача пока не поддерживается в облачной беседе. Она не запущена.")
                if state == "unknown" and self.bridge.claim_notice(job["operation"], "unknown"):
                    await self.m._send(
                        job["channel"], job["chat"],
                        "Не удалось подтвердить результат или доставку. Автоматически повторять запрос не буду. Проверьте /delivery.",
                    )
        except ConversationUnavailable:
            failed = True
            if self.bridge.claim_notice(job["operation"], "archived"):
                await self.m._send(job["channel"], job["chat"], "Беседа в архиве или недоступна. Запрос сохранён и приостановлен.")
        except (BridgeUnavailable, CatalogUnavailable):
            failed = True
            if self.bridge.claim_notice(job["operation"], "offline"):
                await self.m._send(job["channel"], job["chat"], "Не удалось связаться с облаком. Запрос сохранён, проверю позже.")
        except Exception as exc:  # noqa: BLE001 - retain the task and retry with backoff
            failed = True
            logger.warning("cloud messenger job failed: {}", type(exc).__name__)
            with contextlib.suppress(Exception):
                if self.bridge.claim_notice(job["operation"], "internal"):
                    await self.m._send(job["channel"], job["chat"], "Не удалось обработать запрос. Он сохранён, проверю позже.")
        finally:
            try:
                self.bridge.defer(job["operation"], failed=failed)
            finally:
                self._running.discard(job["operation"])

    def _result_chunks(self, result: dict[str, Any]) -> tuple[str, ...]:
        files = wire_files(parse_files(result.get("files", [])))
        return tuple(self._parts(str(result.get("text") or ""))) + tuple(
            json.dumps({"type": "file", **item}, ensure_ascii=False) for item in files
        )

    async def _recovery_command(
        self, msg: InboundMessage, account: str, catalog: dict[str, Any], command: str, text: str,
    ) -> None:
        scope = (account, msg.channel, msg.sender_id, msg.chat_id)
        if command == "/delivery":
            jobs = self.bridge.recovery_jobs(*scope)
            self._recovery_menus[scope] = jobs
            labels = {"accepted": "ждёт результата", "unknown": "неизвестный исход", "held_voice": "голосовое сохранено, не обработано", "held_files": "вложения сохранены, не обработаны", "delivery_full": "история доставок заполнена"}
            lines = [f"{i}. {labels[job['state']]} · {job['message']}" for i, job in enumerate(jobs, 1)]
            note = "Проверить доставку: /delivery_retry номер\n" + ("\n".join(lines) or "Нет ожидающих доставок.")
            await self.m._send(msg.channel, msg.chat_id, note)
            return
        words = text.split()
        if command == "/delivery_retry":
            index = int(words[1]) - 1 if len(words) == 2 and words[1].isdigit() else -1
            jobs = self._recovery_menus.get(scope, [])
            if not 0 <= index < len(jobs):
                await self.m._send(msg.channel, msg.chat_id, "Сначала откройте список через /delivery.")
                return
            job = jobs[index]
            if job["state"] == "delivery_full":
                await self.m._send(msg.channel, msg.chat_id, "История доставок заполнена. Обратитесь в поддержку.")
                return
            if job["state"] in {"held_voice", "held_files"}:
                await self.m._send(msg.channel, msg.chat_id, "Вложения сохранены, но пока не обработаны. Автоматически запускать задачу не буду.")
                return
            if job["operation"] in self._running:
                await self.m._send(msg.channel, msg.chat_id, "Доставка ещё проверяется. Попробуйте позже.")
                return
            if job["conversation"] not in self.bridge.picker._active(account, catalog):
                raise ConversationUnavailable("conversation unavailable")
            result = await self._worker(job, "status")
            if result.get("state") not in {"completed", "failed"} or not result.get("mid"):
                await self.m._send(msg.channel, msg.chat_id, "Результат задачи пока неизвестен. Проверьте историю беседы перед новым запросом.")
                return
            chunks = self._result_chunks(result)
            response_job = {**job, "response_mid": str(result["mid"])}
            response = await self._prepare_delivery(response_job, chunks)
            parts = response.get("parts")
            if not isinstance(parts, list):
                raise BridgeUnavailable("delivery state unavailable")
            self.bridge.delivery.prepare(account, msg.channel, msg.sender_id, msg.chat_id, job["operation"], chunks)
            for part in parts:
                if isinstance(part, dict) and part.get("state") == "sent" and part.get("remote_id"):
                    self.bridge.delivery.confirm_sent(
                        account, msg.channel, msg.sender_id, msg.chat_id, job["operation"], chunks,
                        int(part["part"]), str(part["remote_id"]),
                    )
            uncertain = next((p for p in parts if isinstance(p, dict) and p.get("state") in {"sending", "unknown"}), None)
            if uncertain is None:
                self.bridge.requeue_recovered(job["operation"])
                await self.m._send(msg.channel, msg.chat_id, "Состояние сверено. Подтверждённые части повторно не отправляются.")
                return
            token = self.bridge.issue_recovery(job, int(uncertain["part"]), response_job["response_mid"], chunks)
            await self.m._send(
                msg.channel, msg.chat_id,
                "Эта часть могла уже прийти. Проверьте историю: повтор может создать дубликат. "
                f"Если её нет, подтвердите: /delivery_confirm {token}",
            )
            return
        if len(words) != 2 or len(words[1]) > 100:
            await self.m._send(msg.channel, msg.chat_id, "Сначала запросите подтверждение через /delivery.")
            return
        record = self.bridge.recovery(words[1], *scope)
        if record is None or record["operation"] in self._running:
            await self.m._send(msg.channel, msg.chat_id, "Подтверждение недоступно или истекло. Откройте /delivery снова.")
            return
        if record["conversation"] not in self.bridge.picker._active(account, catalog):
            raise ConversationUnavailable("conversation unavailable")
        result = await self._worker(record, "status")
        chunks = self._result_chunks(result)
        digest = hashlib.sha256(json.dumps(list(chunks), ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        if result.get("state") not in {"completed", "failed"} or result.get("mid") != record["mid"] or digest != record["chunks_digest"]:
            await self.m._send(msg.channel, msg.chat_id, "Результат изменился. Откройте /delivery и проверьте его снова.")
            return
        response_job = {**record, "response_mid": record["mid"]}
        body = {**self._delivery_key(response_job), "part": record["part"], "decision": record["decision"]}
        if not self.bridge.mark_recovery_confirmed(words[1], *scope):
            raise BridgeUnavailable("confirmation expired")
        await self._relay_delivery(response_job, "retry", body)
        self.bridge.delivery.retry_unknown(account, msg.channel, record["operation"], record["part"], record["decision"])
        self.bridge.requeue_recovered(record["operation"])
        await self.m._send(msg.channel, msg.chat_id, "Повтор выбранной части поставлен в очередь.")

    async def reject_legacy_action(self, msg: InboundMessage) -> None:
        try:
            await self._identity(msg)
        except BridgeUnavailable:
            return
        await self.m._send(msg.channel, msg.chat_id, "Эта кнопка пока недоступна в облачной беседе. Используйте /chats.")

    async def handle(self, msg: InboundMessage) -> bool:
        if msg.is_group or msg.channel not in {"telegram", "max"}:
            return False
        text = msg.text.strip()
        command = text.split(maxsplit=1)[0].lower().split("@", 1)[0] if text else ""
        supported = {"/chats", "/chat", "/delivery", "/delivery_retry", "/delivery_confirm"}
        try:
            account, key, base, _token = await self._identity(msg)
            if command.startswith("/") and command not in supported:
                await self.m._send(msg.channel, msg.chat_id, "Эта команда пока недоступна в облачной беседе. Откройте /chats или продолжите текстом.")
                return True
            if msg.files:
                message_id = msg.message_id or "unidentified_" + secrets.token_urlsafe(18)
                job = self.bridge.hold_attachments(account, msg.channel, msg.sender_id, msg.chat_id, message_id, text, msg.files)
                notice = (
                    "Голосовое сохранено, но пока не обработано: распознавание в облачной беседе ещё недоступно."
                    if job["state"] == "held_voice" else
                    "Вложения сохранены, но пока не обработаны: передача файлов между устройствами ещё недоступна."
                )
                await self.m._send(msg.channel, msg.chat_id, notice)
                return True
            catalog = await self._catalog(account, key, base, msg)
            await self._sync_topics(account, msg, catalog)
            items = [r for r in catalog["conversations"] if isinstance(r, dict) and r.get("cid") and not r.get("archived") and not r.get("deleted")]
            menu_key = (account, msg.channel, msg.sender_id, msg.chat_id)
            if command in {"/delivery", "/delivery_retry", "/delivery_confirm"}:
                await self._recovery_command(msg, account, catalog, command, text)
                return True
            if command == "/chats":
                self._menus[menu_key] = [str(row["cid"]) for row in items]
                lines = [f"{i}. {str(row.get('title') or 'Беседа')[:80]}" for i, row in enumerate(items, 1)]
                await self.m._send(msg.channel, msg.chat_id, "Выберите беседу командой /chat номер:\n" + "\n".join(lines))
                return True
            if command == "/chat":
                words = text.split()
                index = int(words[1]) - 1 if len(words) == 2 and words[1].isdigit() else -1
                choices = self._menus.get(menu_key, [])
                if not 0 <= index < len(choices):
                    await self.m._send(msg.channel, msg.chat_id, "Сначала откройте список командой /chats.")
                    return True
                if msg.channel == "telegram":
                    chat, topic = split_chat(msg.chat_id)
                    if topic and chat == msg.sender_id:
                        self.topics.register(account, msg.sender_id, chat, choices[index], topic, catalog)
                self.bridge.picker.choose(account, msg.channel, msg.sender_id, msg.chat_id, choices[index], catalog)
                await self.m._send(msg.channel, msg.chat_id, "Беседа выбрана.")
                return True
            if not text and not msg.files:
                return True
            if not msg.message_id:
                await self.m._send(msg.channel, msg.chat_id, "Не удалось подтвердить номер сообщения. Отправьте его ещё раз.")
                return True
            if msg.channel == "telegram" and not self.bridge.picker.selected(account, msg.channel, msg.sender_id, msg.chat_id, catalog):
                chat, topic = split_chat(msg.chat_id)
                if topic:
                    for row in self.m.picker.topics.file.load().values():
                        if (isinstance(row, dict) and row.get("account") == account and row.get("owner") == msg.sender_id
                            and row.get("chat") == chat and row.get("topic") == topic and row.get("cid") and not row.get("archived")):
                            self.bridge.picker.choose(account, msg.channel, msg.sender_id, msg.chat_id, row["cid"], catalog)
                            break
            job = self.bridge.record(account, msg.channel, msg.sender_id, msg.chat_id, msg.message_id, text, catalog, msg.files)
            if self.bridge.claim_notice(job["operation"], "recorded"):
                await self.m._send(msg.channel, msg.chat_id, "Сообщение сохранено. Проверяю облачную беседу и начинаю обработку.")
            return True
        except (DeliveryJournalFull, DeliveryFull):
            await self.m._send(msg.channel, msg.chat_id, "История доставок заполнена. Обратитесь в поддержку; повторять автоматически не буду.")
            return True
        except StorageFull:
            await self.m._send(msg.channel, msg.chat_id, "Не удалось сохранить запрос: хранилище ожидающих сообщений или вложений заполнено. Попробуйте позже.")
            return True
        except AttachmentError:
            await self.m._send(msg.channel, msg.chat_id, "Проверка вложений не прошла: допускается до четырёх файлов по 5 МБ, всего до 10 МБ.")
            return True
        except DeliveryConflict:
            await self.m._send(msg.channel, msg.chat_id, "Не удалось безопасно сверить доставку. Проверьте историю перед повтором.")
            return True
        except (BridgeUnavailable, CatalogUnavailable):
            await self.m._send(msg.channel, msg.chat_id, "Не удалось получить облачные беседы. Проверьте синхронизацию и попробуйте позже.")
            return True
        except (JobConflict, ValueError):
            await self.m._send(msg.channel, msg.chat_id, "Беседа недоступна или её привязка требует проверки. Выберите активную через /chats.")
            return True
