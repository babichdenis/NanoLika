"""Private-chat controls. Only authenticated channel input can change preferences."""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Any

from nanomuse.channels.base import InboundMessage
from nanomuse.channels.store import _Watched

MODES = {"full-access", "workspace-write", "read-only"}
COPY = {
    "ru": {
        "expired": "Кнопка устарела. Открыла новое меню; выберите действие ещё раз.",
        "private_only": "Настройки и баланс доступны в личном чате с Ликой. Откройте его и отправьте /start.",
        "menu": "Меню Лики",
        "permissions": "Разрешения",
        "balance": "Баланс",
        "pay": "Оплата и тарифы",
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
        "expired": "This button expired. A new menu is open; choose the action again.",
        "private_only": "Open a private chat with Lika and send /start to use settings and balance.",
        "menu": "Lika menu",
        "permissions": "Permissions",
        "balance": "Balance",
        "pay": "Payment and plans",
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
        "expired": "按钮已过期，已打开新菜单，请重新选择。",
        "private_only": "请在与 Lika 的私聊中发送 /start，然后使用设置和余额。",
        "menu": "Lika 菜单",
        "permissions": "权限",
        "balance": "余额",
        "pay": "付款与套餐",
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
        manager.svc.app.sentinel.channel_mode = self.preferences.mode

    async def configure(self, channel: Any) -> None:
        if channel.name not in {"telegram", "max"}:
            return
        lang = self.m.lang if self.m.lang in COPY else "ru"
        c = COPY[lang]
        commands = [
            {"command": key, "description": c[label]}
            for key, label in (
                ("menu", "menu"),
                ("permissions", "permissions"),
                ("balance", "balance"),
                ("pay", "pay"),
                ("language", "language"),
            )
        ]
        commands += [
            {"command": "start", "description": "nanoLika"},
            {"command": "help", "description": "Help"},
            {"command": "status", "description": "Connection"},
            {"command": "computer", "description": "Computer"},
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
        if action in {"home", "permissions", "balance", "language"}:
            return {"menu": "nav:" + action}
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
            await channel._call(
                "POST",
                "/messages",
                params={"chat_id": msg.chat_id},
                json={
                    "text": text,
                    "attachments": [{"type": "inline_keyboard", "payload": {"buttons": buttons}}],
                },
            )

    async def show(self, msg: InboundMessage, page: str = "home", note: str = "") -> None:
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
        else:
            rows = [
                row("permissions", "permissions"),
                row("balance", "balance"),
                [(c["pay"], "https://nanolika.ru/pricing.html")],
                row("language", "language"),
            ]
        if page != "home":
            rows.append(row("back", "home"))
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
            "/permissions": "permissions",
            "/balance": "balance",
            "/language": "language",
            "/pay": "pay",
        }
        if command not in pages or msg.channel not in {"telegram", "max"}:
            return False
        if msg.is_group or not self.owner(msg):
            await self.m._send(msg.channel, msg.chat_id, COPY[self.language(msg)]["private_only"])
            return True
        await self.perform(msg, pages[command])
        return True

    def private_owner(self, msg: InboundMessage) -> bool:
        if not self.m._allowed(msg) or not self.owner(msg):
            return False
        entry = self.m.pairing.approved(msg.channel).get(msg.sender_id, {})
        chat = str(entry.get("chat_id") or msg.sender_id)
        return msg.chat_id == chat or (msg.channel == "telegram" and msg.chat_id == msg.sender_id)

    async def callback(self, channel: str, chat: str, sender: str, value: dict) -> bool:
        if "menu" not in value:
            return False
        probe = InboundMessage(channel=channel, chat_id=chat, sender_id=sender)
        if not self.private_owner(probe):
            return True
        token = str(value["menu"])
        if token.startswith("nav:"):
            action = token[4:]
            if action in {"home", "permissions", "balance", "language"}:
                await self.perform(probe, action)
            return True
        entry = self.tickets.get(token)
        if entry is None or entry[0] < time.monotonic():
            self.tickets.pop(token, None)
            c = COPY[self.language(probe)]
            await self.show(probe, "permissions", c["expired"])
            return True
        _, msg, action = entry
        if (msg.channel, msg.chat_id, msg.sender_id) != (channel, chat, sender):
            return True
        self.tickets.pop(token, None)
        await self.perform(msg, action)
        return True

    async def perform(self, msg: InboundMessage, action: str) -> None:
        c = COPY[self.language(msg)]
        thread = self.m._thread_for(msg)
        if action in {"home", "permissions", "language"}:
            await self.show(msg, action)
        elif action == "pay":
            await self.show(msg, note="https://nanolika.ru/pricing.html")
        elif action == "balance":
            await self.show(msg, note=await self.balance(msg))
        elif action == "inherit":
            self.preferences.put(thread, mode="")
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
            if getattr(self.m.svc.settings.channels, "shared_thread", False):
                await self.show(msg, note=c["remote_old"])
                return
            self.preferences.put(thread, mode=mode)
            note = c["saved"] + ": " + c[mode] + "."
            primary = self.m.svc.hub.primary()
            if primary is not None:
                try:
                    result = await self.m.svc.hub.call(
                        str(primary["id"]),
                        "channel_permissions",
                        {"conversation": thread, "channel": msg.channel, "mode": mode},
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
