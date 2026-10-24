"""Private-chat controls. Only authenticated channel input can change preferences."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from nanomuse.channels.base import InboundMessage
from nanomuse.channels.store import _Watched
from nanomuse.channels.telegram_topics import physical_chat, split_chat
from nanomuse.logger import logger

MODES = {"full-access", "workspace-write", "read-only"}
COPY = {
    "ru": {
        "open_telegram": "Открыть в Telegram",
        "open_max": "Продолжить в MAX",
        "opened_channel": "Беседа «{title}» выбрана в {channel}.",
        "unknown_command": "Эта команда не поддерживается. Выберите действие в меню: беседы создаются кнопкой, без обращения к модели.",
        "new": "Новая беседа",
        "settings": "Настройки",
        "account": "Кабинет ↗",
        "chats": "Чаты",
        "expired": "Кнопка устарела. Открыла новое меню; выберите действие ещё раз.",
        "private_only": "Настройки и баланс доступны в личном чате с Ликой. Откройте его и отправьте /start.",
        "menu": "Меню Лики",
        "permissions": "Разрешения",
        "balance": "Баланс",
        "models": "Модели",
        "models_intro": "Выбери модель для чата. Она работает на всех устройствах.",
        "models_current": "сейчас",
        "models_how": "Пришли номер: /model 3",
        "models_saved": "Модель выбрана.",
        "models_bad": "Такого номера нет в списке.",
        "models_empty": "Список моделей пуст.",
        "models_error": "Не удалось получить модели. Попробуй ещё раз.",
        "models_more": "Показаны первые 15 из {total} — остальные выбираются в приложении или в кабинете.",
        "models_locked": "Ещё {count} моделей открываются с тарифом — они дороже в закупке.",
        "more": "Дальше",
        "rename_usage": "Пришли новое название после команды: /rename Поездка в Сочи",
        "rename_done": "Название сохранено.",
        "rename_failed": "Не удалось переименовать чат.",
        "delete_prompt": "Удалить этот чат? Он исчезнет на всех ваших устройствах.",
        "delete_done": "Чат удалён.",
        "delete_failed": "Не удалось удалить чат.",
        "delete_button": "Удалить чат",
        "cancel": "Отмена",
        "previous": "Назад",
        "language": "Язык",
        "back": "Назад",
        "full-access": "Полный доступ",
        "workspace-write": "Запись в рабочей папке",
        "read-only": "Только чтение",
        "intro": "Выберите доступ для этой беседы. Он не меняет разрешения других пользователей.",
        "full_note": "Полный доступ: Лика сможет выполнять доступные ей действия без повторных вопросов, включая изменение файлов и отправку данных. Ограничения системы и инструментов сохраняются. Включить?",
        "confirm": "Включить полный доступ",
        "saved": "Сохранено для этой беседы",
        "default": "Как настроено в приложении",
        "local": "Локальный обработчик",
        "remote_old": "На компьютере этот режим пока не применён: установленная версия не поддерживает смену доступа из мессенджера. До обновления действуют настройки приложения.",
        "balance_error": "Баланс сейчас недоступен. Откройте личный кабинет на сайте.",
        "no_money": "Сервер не вернул денежный баланс. Сумму не подставляю.",
        "balance_label": "Остаток",
        "tokens": "Остаток текстовых токенов",
        "demo": "Тестовые платежи не меняют рабочий баланс.",
        "lang_saved": "Язык ответов сохранён для этой беседы.",
    },
    "en": {
        "open_telegram": "Open in Telegram",
        "open_max": "Continue in MAX",
        "opened_channel": "Conversation “{title}” selected in {channel}.",
        "unknown_command": "This command is not supported. Choose an action in the menu; creating a chat does not call the model.",
        "new": "New chat",
        "settings": "Settings",
        "account": "Account ↗",
        "chats": "Chats",
        "expired": "This button expired. A new menu is open; choose the action again.",
        "private_only": "Open a private chat with Lika and send /start to use settings and balance.",
        "menu": "Lika menu",
        "permissions": "Permissions",
        "balance": "Balance",
        "models": "Models",
        "models_intro": "Pick the model for the chat. It works on all your devices.",
        "models_current": "now",
        "models_how": "Send the number: /model 3",
        "models_saved": "Model chosen.",
        "models_bad": "There is no such number in the list.",
        "models_empty": "The list of models is empty.",
        "models_error": "Could not load the models. Try again.",
        "models_more": "Showing the first 15 of {total} — the rest are in the app or the cabinet.",
        "models_locked": "{count} more open with a plan — they cost more to run.",
        "more": "Next",
        "rename_usage": "Send the new name after the command: /rename Trip to Sochi",
        "rename_done": "Name saved.",
        "rename_failed": "Could not rename the chat.",
        "delete_prompt": "Delete this chat? It disappears from all your devices.",
        "delete_done": "Chat deleted.",
        "delete_failed": "Could not delete the chat.",
        "delete_button": "Delete chat",
        "cancel": "Cancel",
        "previous": "Back",
        "language": "Language",
        "back": "Back",
        "full-access": "Full access",
        "workspace-write": "Write in working folder",
        "read-only": "Read only",
        "intro": "Choose access for this conversation. Other users keep their permissions.",
        "full_note": "Full access runs available actions without repeated questions, including file changes and sending data. System and tool restrictions remain. Enable it?",
        "confirm": "Enable full access",
        "saved": "Saved for this conversation",
        "default": "As configured in the app",
        "local": "Local runtime",
        "remote_old": "This mode has not been applied on the computer: the installed version cannot change access from a messenger. App settings remain in force until updated.",
        "balance_error": "Balance is unavailable. Open your account on the website.",
        "no_money": "The server returned no money balance. No amount is assumed.",
        "balance_label": "Remaining balance",
        "tokens": "Remaining text tokens",
        "demo": "Test payments do not change the real balance.",
        "lang_saved": "Reply language saved for this conversation.",
    },
    "zh": {
        "open_telegram": "在 Telegram 中打开",
        "open_max": "在 MAX 中继续",
        "opened_channel": "已在 {channel} 中选择对话「{title}」。",
        "unknown_command": "不支持此命令。请在菜单中选择操作；创建对话不会调用模型。",
        "new": "新对话",
        "settings": "设置",
        "account": "账户 ↗",
        "chats": "对话",
        "expired": "按钮已过期，已打开新菜单，请重新选择。",
        "private_only": "请在与 Lika 的私聊中发送 /start，然后使用设置和余额。",
        "menu": "Lika 菜单",
        "permissions": "权限",
        "balance": "余额",
        "models": "模型",
        "models_intro": "选择聊天用的模型。它在所有设备上生效。",
        "models_current": "当前",
        "models_how": "发送编号：/model 3",
        "models_saved": "模型已选择。",
        "models_bad": "列表里没有这个编号。",
        "models_empty": "模型列表是空的。",
        "models_error": "无法获取模型。请再试一次。",
        "models_more": "仅显示前 15 个（共 {total}）——其余在应用或账户中选择。",
        "models_locked": "另有 {count} 个模型需套餐才能开启。",
        "more": "下一页",
        "rename_usage": "发送新名称：/rename 去索契",
        "rename_done": "名称已保存。",
        "rename_failed": "无法重命名聊天。",
        "delete_prompt": "删除此聊天？它会从你所有设备上消失。",
        "delete_done": "聊天已删除。",
        "delete_failed": "无法删除聊天。",
        "delete_button": "删除聊天",
        "cancel": "取消",
        "previous": "上一页",
        "language": "语言",
        "back": "返回",
        "full-access": "完全访问",
        "workspace-write": "在工作目录中写入",
        "read-only": "只读",
        "intro": "为当前对话选择权限，不改变其他用户的权限。",
        "full_note": "完全访问会直接执行工具可用的操作，包括修改文件和发送数据，不再逐步询问。系统及工具限制仍然有效。是否启用？",
        "confirm": "启用完全访问",
        "saved": "已为当前对话保存",
        "default": "沿用应用设置",
        "local": "本地运行环境",
        "remote_old": "电脑上的版本暂不支持从聊天中更改权限，更新前继续使用应用设置。",
        "balance_error": "暂时无法获取余额，请在网站上打开账户。",
        "no_money": "服务器未返回金额，不显示推测值。",
        "balance_label": "剩余余额",
        "tokens": "剩余文本令牌",
        "demo": "测试支付不会改变实际余额。",
        "lang_saved": "已保存当前对话的回复语言。",
    },
}


class Preferences(_Watched):
    def get(self, thread: str) -> dict[str, str]:
        entry = self.load().get(thread, {})
        return entry if isinstance(entry, dict) else {}

    def put(self, thread: str, **changes: str) -> None:
        data = dict(self.load())
        data[thread] = {**self.get(thread), **changes}
        self.save(data)

    def mode(self, thread: str) -> str:
        mode = self.get(thread).get("mode", "")
        return mode if mode in MODES else ""


class ChatMenu:
    def __init__(self, manager: Any):
        self.m = manager
        self.preferences = Preferences(Path(manager.svc.data_dir) / "channels" / "preferences.json")
        self.tickets: dict[str, tuple[float, InboundMessage, str]] = {}
        self.chat_tickets = _Watched(
            Path(manager.svc.data_dir) / "channels" / "chat-menu-tickets.json"
        )
        manager.svc.app.sentinel.channel_mode = self.preferences.mode

    def access_scope(self, msg: InboundMessage) -> str:
        account = getattr(getattr(self.m.svc, "sync", None), "account_id", "")
        if not account or not self.private_owner(msg):
            return ""
        identity = "\n".join(
            (account, msg.channel, physical_chat(msg.channel, msg.chat_id), msg.sender_id)
        )
        return "access-" + hashlib.sha256(identity.encode()).hexdigest()

    def inherit_access(self, msg: InboundMessage, thread: str) -> None:
        """Carry the paired owner's choice into selected chats, never other accounts."""
        scope = self.access_scope(msg)
        if not scope:
            return
        current = self.preferences.get(thread)
        if "mode" in current and not current.get("access_source"):
            return  # an explicit per-conversation choice, including app defaults
        target = getattr(self.m.svc, "threads", {}).get(thread)
        mine = getattr(self.m.svc.sync, "_mine", lambda _: False)
        if target is None or not mine(target):
            return
        default = self.preferences.get(scope)
        if "mode" not in default:
            from nanomuse.channels.manager import thread_id_for

            legacy_id = thread_id_for(msg.channel, physical_chat(msg.channel, msg.chat_id))
            legacy = self.m.svc.threads.get(legacy_id)
            owner_of = getattr(self.m.svc.sync, "owner_of", lambda _: "")
            if legacy is None or owner_of(legacy_id) != self.m.svc.sync.account_id:
                return
            old = self.preferences.get(legacy_id)
            if "mode" not in old:
                return
            self.preferences.put(scope, mode=old["mode"])
            default = self.preferences.get(scope)
        self.preferences.put(thread, mode=default["mode"], access_source=scope)

    async def configure(self, channel: Any) -> None:
        if channel.name not in {"telegram", "max"}:
            return
        lang = self.m.lang if self.m.lang in COPY else "ru"
        c = COPY[lang]
        commands = [
            {"command": key, "description": c[label]}
            for key, label in (("chats", "chats"), ("new", "new"), ("menu", "menu"))
        ]
        if channel.name == "telegram":
            await channel._call("setMyCommands", {"commands": commands})
            await channel._call("setChatMenuButton", {"menu_button": {"type": "commands"}})
        else:
            await channel._call(
                "PATCH",
                "/me/commands",
                json={
                    "commands": [
                        {"name": item["command"], "description": item["description"]}
                        for item in commands
                    ]
                },
            )

    def language(self, msg: InboundMessage) -> str:
        thread = self.m._thread_for(msg)
        default = self.m.lang if self.m.lang in COPY else "ru"
        return self.preferences.get(thread).get("language", default)

    def ticket(self, msg: InboundMessage, action: str) -> dict[str, str]:
        if action in {"home", "settings", "permissions", "balance", "language", "chats", "models"}:
            return {"menu": "nav:" + action}
        if action.startswith("chat:"):
            now = time.time()
            saved = {
                k: v
                for k, v in self.chat_tickets.load().items()
                if isinstance(v, dict) and v.get("expires", 0) > now
            }
            while len(saved) >= 2048:
                saved.pop(next(iter(saved)))
            token = "c:" + secrets.token_hex(8)
            saved[token] = {
                "expires": now + 7 * 86400,
                "channel": msg.channel,
                "chat": msg.chat_id,
                "sender": msg.sender_id,
                "account": self.m.svc.sync.account_id,
                "action": action,
            }
            self.chat_tickets.save(saved)
            return {"menu": token}
        if len(self.tickets) >= 256:
            self.tickets.pop(next(iter(self.tickets)))
        token = secrets.token_hex(8)
        self.tickets[token] = (time.monotonic() + 900, msg, action)
        return {"menu": token}

    async def send(self, msg: InboundMessage, text: str, rows: list[list[tuple[str, Any]]]) -> None:
        channel = self.m.channels.get(msg.channel)
        if channel is None:
            return
        if msg.channel == "telegram":
            buttons = [
                [
                    {
                        "text": label,
                        **(
                            {"url": value}
                            if isinstance(value, str)
                            else {"callback_data": "m:" + value["menu"]}
                        ),
                    }
                    for label, value in row
                ]
                for row in rows
            ]
            if msg.message_id and not msg.text:
                try:
                    await channel._call(
                        "editMessageText",
                        {
                            "chat_id": msg.chat_id,
                            "message_id": msg.message_id,
                            "text": text,
                            "reply_markup": {"inline_keyboard": buttons},
                        },
                    )
                    return
                except Exception as exc:
                    if "message is not modified" in str(exc).lower():
                        return
                    logger.warning("channel telegram: menu edit failed ({})", type(exc).__name__)
            await channel._call(
                "sendMessage",
                {
                    "chat_id": msg.chat_id,
                    "text": text,
                    "reply_markup": {"inline_keyboard": buttons},
                },
            )
        elif msg.channel == "max":
            buttons = [
                [
                    {
                        "text": label,
                        **(
                            {"type": "link", "url": value}
                            if isinstance(value, str)
                            else {"type": "callback", "payload": json.dumps(value)}
                        ),
                    }
                    for label, value in row
                ]
                for row in rows
            ]
            if msg.message_id and not msg.text:
                try:
                    await channel._call(
                        "PUT",
                        "/messages",
                        params={"message_id": msg.message_id},
                        json={
                            "text": text,
                            "attachments": [
                                {"type": "inline_keyboard", "payload": {"buttons": buttons}}
                            ],
                        },
                    )
                    return
                except Exception as exc:
                    logger.warning("channel max: menu edit failed ({})", type(exc).__name__)
            await channel._call(
                "POST",
                "/messages",
                params={"chat_id": msg.chat_id},
                json={
                    "text": text,
                    "attachments": [{"type": "inline_keyboard", "payload": {"buttons": buttons}}],
                },
            )

    async def show(self, msg: InboundMessage, page: str = "home", note: str = "", page_no: int = 1) -> None:
        c = COPY[self.language(msg)]

        def row(label: str, action: str) -> list:
            return [(c.get(label, label), self.ticket(msg, action))]

        rows: list = []
        text = c["menu"]
        if page == "permissions":
            mode = self.preferences.mode(self.m._thread_for(msg))
            text = c["intro"] + "\n\n" + c.get(mode, c["default"])
            rows = [
                row(mode, "mode:" + mode)
                for mode in ("full-access", "workspace-write", "read-only")
            ]
            rows.append(row("default", "inherit"))
        elif page == "language":
            text = c["language"]
            rows = [
                [(label, self.ticket(msg, "lang:" + lang))]
                for lang, label in (("ru", "Русский"), ("en", "English"), ("zh", "简体中文"))
            ]
        elif page == "confirm-full":
            text = c["full_note"]
            rows = [row("confirm", "mode-confirm:full-access")]
        elif page == "settings":
            text = c["settings"]
            rows = [row("permissions", "permissions"), row("language", "language")]
        elif page == "models":
            # Кнопки вместо номеров: список листается, как выбор времени, и нажатие
            # сразу выбирает модель. Восемь на экран — столько читается с телефона.
            per_page = 8
            try:
                data = await self._shelf(msg)
            except Exception:
                text = c["models_error"]
                rows = []
            else:
                every = [m for m in (data.get("models") or []) if m.get("kind") == "chat"]
                models = [m for m in every if m.get("available", True)]
                current = ((data.get("shelf") or {}).get("default") or {}).get("chat") or ""
                pages = max(1, (len(models) + per_page - 1) // per_page)
                page_no = min(max(1, page_no), pages)
                start = (page_no - 1) * per_page
                chunk = models[start : start + per_page]
                text = c["models_intro"] + f"\n\n{page_no} / {pages}"
                locked = len(every) - len(models)
                extra = [c["models_locked"].format(count=locked)] if locked else []
                text = text + ("\n" + "\n".join(extra) if extra else "")
                rows = []
                for model in chunk:
                    name = str(model.get("name") or model.get("id"))
                    if model.get("id") == current:
                        name = name + "  ← " + c["models_current"]
                    rows.append([(name[:58], self.ticket(msg, "model:" + str(model.get("id"))))])
                nav: list = []
                if page_no > 1:
                    nav.append((c.get("previous", "←"), self.ticket(msg, f"model-page:{page_no - 1}")))
                if page_no < pages:
                    nav.append((c.get("more", "→"), self.ticket(msg, f"model-page:{page_no + 1}")))
                if nav:
                    rows.append(nav)
        else:
            rows = [
                [(c["chats"], self.ticket(msg, "chats")), (c["new"], self.ticket(msg, "chat:new"))],
                [
                    (c["models"], self.ticket(msg, "models")),
                    (c["settings"], self.ticket(msg, "settings")),
                ],
                [
                    (c["account"], "https://cloud.nanolika.ru/web/account/?source=messenger"),
                ],
            ]
        if page == "home":
            sync = getattr(self.m.svc, "sync", None)
            cid = sync.cid_of(self.m._thread_for(msg)) if sync else None
            if cid:
                rows.append(
                    [
                        (c["open_telegram"], self.ticket(msg, "chat:open:telegram:" + cid)),
                        (c["open_max"], self.ticket(msg, "chat:open:max:" + cid)),
                    ]
                )
        if page != "home":
            rows.append(
                row(
                    "back",
                    "settings" if page in {"permissions", "language", "confirm-full"} else "home",
                )
            )
        await self.send(msg, (note + "\n\n" if note else "") + text, rows)

    def owner(self, msg: InboundMessage) -> bool:
        return bool(
            self.m.pairing.is_approved(msg.channel, msg.sender_id)
            or msg.sender_id in self.m.store.config(msg.channel).allow_from
        )

    async def inbound(self, msg: InboundMessage) -> bool:
        command = msg.text.split()[0].lower().split("@")[0] if msg.text.strip() else ""
        pages = {
            "/menu": "home",
            "/chats": "chats",
            "/chat": "chats",
            "/new": "chat:new",
            "/newchat": "chat:new",
            "/new_chat": "chat:new",
            "/newsession": "chat:new",
            "/new_session": "chat:new",
            "/permissions": "permissions",
            "/balance": "balance",
            "/language": "language",
            "/pay": "pay",
            "/model": "models",
            "/rename": "chat-rename",
            "/del": "chat-delete",
            "/models": "models",
        }
        if msg.channel not in {"telegram", "max"}:
            return False
        if command not in pages:
            if command in {"/start", "/help", "/status", "/computer"}:
                return False
            if re.fullmatch(r"/[a-z][a-z0-9_]{0,31}", command) and self.private_owner(msg):
                await self.show(msg, note=COPY[self.language(msg)]["unknown_command"])
                return True
            return False
        if command in {"/model", "/models"} and len(msg.text.split()) > 1:
            pages[command] = "model:" + msg.text.split()[1]
        if command == "/rename" and len(msg.text.split()) > 1:
            pages[command] = "chat-rename:" + " ".join(msg.text.split()[1:])
        if msg.is_group or not self.owner(msg):
            await self.m._send(msg.channel, msg.chat_id, COPY[self.language(msg)]["private_only"])
            return True
        logger.info("channel {}: private menu command received ({})", msg.channel, command)
        await self.perform(msg, pages[command])
        return True

    def private_owner(self, msg: InboundMessage) -> bool:
        if not self.m._allowed(msg) or not self.owner(msg):
            return False
        entry = self.m.pairing.approved(msg.channel).get(msg.sender_id, {})
        chat = str(entry.get("chat_id") or msg.sender_id)
        return physical_chat(msg.channel, msg.chat_id) == chat or (
            msg.channel == "telegram" and physical_chat(msg.channel, msg.chat_id) == msg.sender_id
        )

    async def callback(self, channel: str, chat: str, sender: str, value: dict) -> bool:
        if "menu" not in value:
            return False
        probe = InboundMessage(
            channel=channel,
            chat_id=chat,
            sender_id=sender,
            message_id=str(value.get("_menu_message_id") or ""),
        )
        if not self.private_owner(probe):
            return True
        token = str(value["menu"])
        if token.startswith("nav:"):
            action = token[4:]
            if action in {"home", "settings", "permissions", "balance", "language", "chats", "models"}:
                await self.perform(probe, action)
            return True
        if token.startswith("c:"):
            saved = dict(self.chat_tickets.load())
            entry = saved.get(token)
            if entry is None or entry.get("expires", 0) <= time.time():
                await self.perform(probe, "chats")
                return True
            if (
                entry.get("channel"),
                entry.get("chat"),
                entry.get("sender"),
                entry.get("account"),
            ) != (channel, chat, sender, self.m.svc.sync.account_id):
                return True
            saved.pop(token, None)
            self.chat_tickets.save(saved)
            await self.perform(probe, str(entry["action"]))
            return True
        entry = self.tickets.get(token)
        if entry is None or entry[0] < time.monotonic():
            self.tickets.pop(token, None)
            c = COPY[self.language(probe)]
            await self.show(probe, "home", c["expired"])
            return True
        _, msg, action = entry
        if (msg.channel, msg.chat_id, msg.sender_id) != (channel, chat, sender):
            return True
        self.tickets.pop(token, None)
        await self.perform(replace(msg, message_id=probe.message_id, text=""), action)
        return True

    async def perform(self, msg: InboundMessage, action: str) -> None:
        topics = getattr(getattr(self.m, "picker", None), "topics", None)
        if (
            topics is not None
            and topics.enabled(msg.channel)
            and not split_chat(msg.chat_id)[1]
            and (
                action in {"permissions", "inherit"}
                or action.startswith(("mode:", "mode-confirm:"))
            )
        ):
            await self.m.picker.show(msg)
            return
        if action == "chats" or action.startswith("chat:"):
            await self.m.picker.perform(msg, action)
            return
        c = COPY[self.language(msg)]
        thread = self.m._thread_for(msg)
        if action in {"home", "settings", "permissions", "language"}:
            await self.show(msg, action)
        elif action == "pay":
            await self.show(msg, note="https://nanolika.ru/pricing.html")
        elif action == "balance":
            await self.show(msg, note=await self.balance(msg))
        elif action == "models":
            await self.show(msg, "models")
        elif action.startswith("model-page:"):
            try:
                page_no = int(action.split(":", 1)[1])
            except ValueError:
                page_no = 1
            await self.show(msg, "models", page_no=page_no)
        elif action.startswith("model:"):
            chosen = action.split(":", 1)[1]
            answer = await self.model_pick(msg, chosen)
            await self.show(msg, "models", note=answer)
        elif action == "chat-rename":
            await self.show(msg, note=c["rename_usage"])
        elif action.startswith("chat-rename:"):
            await self.show(msg, note=await self.chat_rename(msg, action.split(":", 1)[1]))
        elif action == "chat-delete":
            await self.show(msg, note=await self.chat_delete_prompt(msg))
        elif action == "chat-delete-confirm":
            await self.show(msg, note=await self.chat_delete(msg))
        elif action == "inherit":
            self.preferences.put(thread, mode="", access_source="")
            if scope := self.access_scope(msg):
                self.preferences.put(scope, mode="")
            await self.show(msg, "permissions", c["default"])
        elif action.startswith("lang:") and action[5:] in COPY:
            self.preferences.put(thread, language=action[5:])
            await self.show(msg, note=COPY[action[5:]]["lang_saved"])
        elif action == "mode:full-access":
            await self.show(msg, "confirm-full")
        elif action.startswith(("mode:", "mode-confirm:")):
            mode = action.split(":", 1)[1]
            if mode not in MODES or (
                mode == "full-access" and action != "mode-confirm:full-access"
            ):
                return
            if getattr(getattr(self.m.svc.settings, "channels", None), "shared_thread", False):
                await self.show(msg, note=c["remote_old"])
                return
            self.preferences.put(thread, mode=mode, access_source="")
            if scope := self.access_scope(msg):
                self.preferences.put(scope, mode=mode)
            note = c["saved"] + ": " + c[mode] + "."
            primary = self.m.svc.hub.primary()
            if primary is not None:
                try:
                    result = await self.m.svc.hub.call(
                        str(primary["id"]),
                        "channel_permissions",
                        {
                            "conversation": thread,
                            "sync_cid": self.m.svc.sync.cid_of(thread)
                            if getattr(self.m.svc, "sync", None)
                            else None,
                            "channel": msg.channel,
                            "mode": mode,
                        },
                        timeout=12,
                    )
                    if result.get("mode") != mode:
                        raise ValueError("not applied")
                except Exception:  # authenticated local preference remains usable offline
                    note += "\n\n" + c["remote_old"]
            else:
                note += "\n" + c["local"]
            if mode == "full-access":
                for approval in list(
                    getattr(self.m, "_asks", {}).get(f"{msg.channel}:{msg.chat_id}", [])
                ):
                    await self.m._decide(msg.channel, msg.chat_id, approval, True)
            await self.show(msg, "permissions", note)

    async def _cloud_access(self, msg: InboundMessage) -> tuple[str, str]:
        """The key this chat acts with and the relay it talks to (as `balance` does)."""
        signin = getattr(self.m, "_cloud_key_for", None)
        key = ""
        if signin:
            key, _ = await signin(msg.channel, msg)
        if not key or key == "!":
            if str(
                self.m.store.config(msg.channel).settings.get("cloud_per_chat") or ""
            ).lower() not in {"1", "true", "yes"}:
                from nanomuse.cloud import CLOUD_KEY

                key = self.m.svc.app.vault.get(CLOUD_KEY) or ""
        return key, str(self.m.svc.settings.cloud.base_url or "").rstrip("/")

    async def _shelf(self, msg: InboundMessage) -> dict:
        """The account's own menu: which models are on the shelf and which is default."""
        import httpx

        c = COPY[self.language(msg)]
        key, base = await self._cloud_access(msg)
        if not key or not base:
            raise RuntimeError(c["models_error"])
        async with httpx.AsyncClient(timeout=20) as client:
            answer = await client.get(base + "/v1/me/models", headers={"Authorization": "Bearer " + key})
            answer.raise_for_status()
            return answer.json()

    async def models_list(self, msg: InboundMessage) -> str:
        """The chat models as a numbered list, with the one answering now marked.

        0.29: choosing a model used to live only in the app and on the web page. A person
        in a chat should not have to open anything to switch, so the same shelf is printed
        here and `/model 3` writes it back to the relay, which every device then sees."""
        c = COPY[self.language(msg)]
        try:
            data = await self._shelf(msg)
        except Exception:
            return c["models_error"]
        every = [m for m in (data.get("models") or []) if m.get("kind") == "chat"]
        models = [m for m in every if m.get("available", True)]
        if not models:
            return c["models_empty"]
        locked = len(every) - len(models)
        current = ((data.get("shelf") or {}).get("default") or {}).get("chat") or ""
        lines = [c["models_intro"], ""]
        for index, model in enumerate(models[:15], start=1):
            mark = "  ← " + c["models_current"] if model.get("id") == current else ""
            lines.append(f"{index}. {model.get('name') or model.get('id')}{mark}")
        hidden = max(0, len(models) - 15)
        if hidden:
            lines.append("")
            lines.append(c["models_more"].format(total=len(models), shown=15))
        if locked:
            lines.append("")
            lines.append(c["models_locked"].format(count=locked))
        lines.append("")
        lines.append(c["models_how"])
        return "\n".join(lines)

    async def model_pick(self, msg: InboundMessage, raw: str) -> str:
        """Pick a model by id (a button) or by number (`/model 3`), then write it back.

        The write sends both halves the relay wants: the chat list that stays on the shelf
        and the one answering by default. A refusal is logged with its reason instead of
        being reported as "could not load" — that lie cost an evening once."""
        import logging

        import httpx

        c = COPY[self.language(msg)]
        raw = str(raw or "").strip()
        try:
            data = await self._shelf(msg)
        except Exception:
            logging.getLogger(__name__).exception("model pick: could not read the shelf")
            return c["models_error"]
        every = [m for m in (data.get("models") or []) if m.get("kind") == "chat"]
        models = [m for m in every if m.get("available", True)]
        chosen: dict | None = None
        if raw.isdigit():
            index = int(raw)
            if 1 <= index <= min(len(models), 15):
                chosen = models[index - 1]
        else:
            chosen = next((m for m in models if str(m.get("id")) == raw), None)
        if chosen is None:
            return c["models_bad"]

        key, base = await self._cloud_access(msg)
        if not key or not base:
            return c["models_error"]
        shelf = data.get("shelf") or {}
        enabled = {kind: list(ids) for kind, ids in (shelf.get("enabled") or {}).items()}
        chat_ids = list(enabled.get("chat") or [])
        model_id = str(chosen.get("id"))
        if model_id not in chat_ids:
            chat_ids.append(model_id)
        body = {"enabled": {"chat": chat_ids}, "default": {"chat": model_id}}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                answer = await client.put(
                    base + "/v1/me/models",
                    headers={"Authorization": "Bearer " + key},
                    json=body,
                )
                if answer.status_code >= 400:
                    logging.getLogger(__name__).warning(
                        "model pick refused: %s %s", answer.status_code, answer.text[:200]
                    )
                    return c["models_error"]
        except Exception:
            logging.getLogger(__name__).exception("model pick failed")
            return c["models_error"]
        return c["models_saved"] + "\n" + str(chosen.get("name") or model_id)

    def _cid_now(self, msg: InboundMessage) -> str:
        """The id of the conversation this chat is showing, from the sync store."""
        sync = getattr(self.m.svc, "sync", None)
        if sync is None:
            return ""
        try:
            return str(sync.cid_of(self.m._thread_for(msg)) or "")
        except Exception:
            return ""

    async def _chat_write(self, msg: InboundMessage, method: str, body: dict | None) -> bool:
        """One call to the relay about the current conversation: rename or delete."""
        import httpx

        key, base = await self._cloud_access(msg)
        cid = self._cid_now(msg)
        if not key or not base or not cid:
            return False
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                answer = await client.request(
                    method,
                    base + "/v1/sync/conversations/" + cid,
                    headers={"Authorization": "Bearer " + key},
                    json=body,
                )
                return answer.status_code < 400
        except Exception:
            import logging

            logging.getLogger(__name__).exception("chat write failed")
            return False

    async def chat_rename(self, msg: InboundMessage, title: str) -> str:
        c = COPY[self.language(msg)]
        title = str(title or "").strip()
        if not title:
            return c["rename_usage"]
        return c["rename_done"] if await self._chat_write(msg, "PATCH", {"title": title[:120]}) else c["rename_failed"]

    async def chat_delete_prompt(self, msg: InboundMessage) -> str:
        c = COPY[self.language(msg)]
        if not self._cid_now(msg):
            return c["delete_failed"]
        return c["delete_prompt"]

    async def chat_delete(self, msg: InboundMessage) -> str:
        c = COPY[self.language(msg)]
        return c["delete_done"] if await self._chat_write(msg, "DELETE", None) else c["delete_failed"]


    async def balance(self, msg: InboundMessage) -> str:
        c = COPY[self.language(msg)]
        try:
            from nanomuse.cloud import CloudClient

            signin = getattr(self.m, "_cloud_key_for", None)
            key, _ = await signin(msg.channel, msg) if signin else ("", "")
            if not key or key == "!":
                # Single-owner runtime uses its account, never another chat's cached key.
                if str(
                    self.m.store.config(msg.channel).settings.get("cloud_per_chat") or ""
                ).lower() in {"1", "true", "yes"}:
                    return c["balance_error"]
                from nanomuse.cloud import CLOUD_KEY

                key = self.m.svc.app.vault.get(CLOUD_KEY)
            if not key:
                return c["balance_error"]
            client = CloudClient(base_url=self.m.svc.settings.cloud.base_url, api_key=key)
            try:
                data = await client.me()
            finally:
                await client.close()
            spend = data.get("spend") or {}
            tokens = data.get("tokens") or {}
            if spend.get("unlimited"):
                return c["balance_label"] + ": ∞\n" + c["demo"]
            if isinstance(spend.get("left"), (int, float)):
                return (
                    c["balance_label"]
                    + ": "
                    + str(round(spend["left"], 4))
                    + " "
                    + str(spend.get("currency") or "CNY")
                    + "\n"
                    + c["demo"]
                )
            if isinstance(tokens.get("remaining"), (int, float)):
                return c["tokens"] + ": " + str(tokens["remaining"]) + "\n" + c["demo"]
            return c["no_money"]
        except Exception:
            return c["balance_error"]
