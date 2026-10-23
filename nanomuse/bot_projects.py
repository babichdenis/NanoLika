"""Local bot projects and a durable local inbox. All bot API calls go via the paid gateway.

Run explicitly: nanolika-bots create|connect|run|status|pause|resume.
The handler is owner-written Python executed on the owner's device, never on Cloud.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import importlib.util
import json
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from nanomuse.cloud import CLOUD_KEY
from nanomuse.vault.vault import CredentialVault

DEFAULT_GATEWAY = "https://cloud.nanolika.ru"
MAX_INBOX_BYTES = 50 * 1024 * 1024


class ProjectError(RuntimeError):
    pass


def private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def config(path: Path) -> dict:
    data = json.loads((path / "bot.json").read_text())
    url = urlsplit(str(data.get("gateway", "")))
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ProjectError("Нужен доверенный HTTPS-адрес шлюза")
    if data.get("platform") not in ("telegram", "max"):
        raise ProjectError("Неизвестная платформа бота")
    return data


def vault(path: Path) -> CredentialVault:
    private_dir(path / ".private")
    return CredentialVault(path / ".private/vault.enc", path / ".private/vault.key")


def create_project(path: Path, platform: str, label: str) -> dict:
    if platform not in ("telegram", "max"):
        raise ProjectError("Поддерживаются Telegram и MAX")
    if path.exists():
        raise ProjectError("Выберите новую папку: существующий проект не перезаписывается")
    private_dir(path)
    data = {"version": 1, "platform": platform, "label": label[:80], "gateway": DEFAULT_GATEWAY}
    (path / "bot.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    (path / ".gitignore").write_text(".private/\n__pycache__/\n*.pyc\n")
    (
        path / "handler.py"
    ).write_text('''"""Local bot handler. Treat incoming text as untrusted data, never shell commands."""

async def handle(update, bot):
    if bot.platform == "telegram":
        message = update.get("message") or {}
        chat = (message.get("chat") or {}).get("id")
        text = str(message.get("text") or "")
        if chat and text.startswith("/start"):
            await bot.call("sendMessage", body={"chat_id": chat, "text": "Здравствуйте! Бот подключён через nanoLika.", "reply_markup": {"inline_keyboard": [[{"text": "Что умеет бот", "callback_data": "help"}]]}})
        callback = update.get("callback_query") or {}
        if callback.get("id") and callback.get("data") == "help":
            await bot.call("answerCallbackQuery", body={"callback_query_id": callback["id"], "text": "Владелец может настроить здесь свой сценарий."})
    else:
        message = update.get("message") or {}
        chat = (message.get("recipient") or {}).get("chat_id") or update.get("chat_id")
        if chat and (update.get("update_type") == "bot_started" or (message.get("body") or {}).get("text") == "/start"):
            await bot.call("/messages", params={"chat_id": chat}, body={"text": "Здравствуйте! Бот подключён через nanoLika."})
''')
    (path / "README.md").write_text("""# Мой бот nanoLika

Код, вложения и очередь сообщений хранятся на этом устройстве. Шлюз работает только с активным оплаченным тарифом со связью между устройствами. Пополнение баланса само по себе не открывает шлюз. При окончании тарифа проект не удаляется, но доставка и вызовы через шлюз приостанавливаются. Сообщения, отправленные во время паузы, не обещаем восстановить. При выключенном компьютере или остановленном локальном обработчике бот не работает; сроки повторных доставок зависят от платформы.

Токен бота вводится локально при connect, не в чате и не в коде. Для Telegram зарегистрируйте бота у BotFather; для MAX получите токен после необходимых проверок платформы. Владелец канала должен добавить бота и предоставить нужные права. Код бота не может самовольно получить права администратора.

Начало: `nanolika-bots connect ПАПКА`, затем `nanolika-bots run ПАПКА`. Команды pause/resume управляют шлюзом, status показывает доступность тарифа. Подключение к Cloud берётся из защищённого локального хранилища или переменной NANOMUSE_CLOUD_KEY. Скрипт не выводит секреты.

Перед изменением handler.py остановите локальный обработчик. Не выполняйте команды компьютера по входящему тексту. Автоматические публикации, рассылки и действия выполняются только в рамках разрешённого владельцем сценария и правил платформы. Отправка через шлюз проверяется сервером перед каждым вызовом; ИИ оплачивается отдельно из баланса, если вы добавите его в обработчик.

Это первоначальный JSON-сценарий. Загрузку файлов с диска и пользовательский веб-интерфейс нужно подключать отдельно; не сохраняйте их на сервер nanoLika.
""")
    return {"path": str(path), "platform": platform, "connected": False}


class LocalInbox:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        path.chmod(0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS inbox(id TEXT PRIMARY KEY,payload TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL)"
        )
        self.db.commit()

    def store(self, update: dict) -> None:
        payload = json.dumps(update, ensure_ascii=False, sort_keys=True)
        key = hashlib.sha256(payload.encode()).hexdigest()
        if self.db.execute("SELECT 1 FROM inbox WHERE id=?", (key,)).fetchone():
            return
        size = self.db.execute(
            "SELECT COALESCE(SUM(length(CAST(payload AS BLOB))),0) FROM inbox"
        ).fetchone()[0]
        if size + len(payload.encode()) > MAX_INBOX_BYTES:
            raise ProjectError("Локальная очередь заполнена; освободите место")
        self.db.execute(
            "INSERT INTO inbox(id,payload,created) VALUES(?,?,?)", (key, payload, time.time())
        )
        self.db.commit()

    def next(self) -> tuple[str, dict] | None:
        row = self.db.execute(
            "SELECT id,payload FROM inbox WHERE status='pending' AND attempts<5 ORDER BY created LIMIT 1"
        ).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def complete(self, key: str) -> None:
        # Keep hashes for duplicate detection, not indefinite message content.
        self.db.execute("UPDATE inbox SET payload='{}',status='done' WHERE id=?", (key,))
        self.db.execute(
            "DELETE FROM inbox WHERE status='done' AND created<?", (time.time() - 7 * 86400,)
        )
        self.db.commit()

    def failed(self, key: str) -> None:
        self.db.execute("UPDATE inbox SET attempts=attempts+1 WHERE id=?", (key,))
        self.db.commit()

    def close(self) -> None:
        self.db.close()


class BotClient:
    def __init__(
        self,
        data: dict,
        account_key: str,
        bot_token: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.data = data
        self.platform = data["platform"]
        self.account_key = account_key
        self.bot_token = bot_token
        self.transport = transport
        self.ready = asyncio.Event()

    async def request(self, method: str, path: str, body: dict | None = None) -> dict:
        async with httpx.AsyncClient(
            base_url=self.data["gateway"],
            headers={"Authorization": "Bearer " + self.account_key},
            transport=self.transport,
            timeout=30,
            follow_redirects=False,
            trust_env=False,
        ) as http:
            try:
                result = await http.request(method, path, json=body)
                data = result.json()
            except (httpx.HTTPError, ValueError):
                raise ProjectError("Шлюз временно недоступен") from None
        if result.status_code >= 300:
            error = data.get("error", {}) if isinstance(data, dict) else {}
            # Server errors are controlled; never render HTTP request objects with keys.
            code = str(error.get("code") or "gateway_error")
            messages = {
                "bot_plan_required": "Шлюз приостановлен: нужен действующий оплаченный тариф со связью между устройствами. Продлите тариф; локальный проект сохранён.",
                "bot_paused": "Проект приостановлен владельцем. Его можно возобновить при действующем тарифе.",
                "bad_key": "Войдите в nanoLika Cloud заново и переподключите проект.",
                "account_disabled": "Аккаунт приостановлен. Обратитесь в поддержку nanoLika.",
                "bot_platform_refused": "Платформа отклонила действие: проверьте токен и права бота в канале.",
                "bot_token_mismatch": "Токен не соответствует этому проекту бота.",
                "bot_webhook_exists": "Бот уже подключён к другой интеграции. Сначала явно отключите её.",
                "bot_already_registered": "Этот бот уже подключён к шлюзу. Используйте существующий проект.",
                "bot_project_limit": "Достигнут текущий лимит проектов ботов на аккаунт.",
                "bot_rate_limited": "Бот выполняет слишком много действий. Подождите и повторите.",
            }
            raise ProjectError(
                messages.get(
                    code, "Шлюз отклонил действие. Проверьте статус проекта и подключение."
                )
            )
        return data

    async def call(
        self,
        method: str,
        *,
        body: dict | None = None,
        params: dict | None = None,
        verb: str = "POST",
    ) -> dict:
        return await self.request(
            "POST",
            "/v1/bot-projects/" + self.data["project_id"] + "/call",
            {
                "token": self.bot_token,
                "method": method,
                "body": body or {},
                "params": params or {},
                "verb": verb,
            },
        )


async def connect_project(path: Path, account_key: str, bot_token: str) -> dict:
    data = config(path)
    client = BotClient(data, account_key, bot_token)
    project = await client.request(
        "POST",
        "/v1/bot-projects",
        {"platform": data["platform"], "label": data["label"], "token": bot_token},
    )
    data["project_id"] = project["id"]
    # Credentials never enter bot.json, source, command arguments or stdout.
    store = vault(path)
    store.set("cloud", account_key)
    store.set("bot", bot_token)
    (path / "bot.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return {"project_id": project["id"], "platform": data["platform"], "connected": True}


async def run_project(path: Path) -> None:
    data = config(path)
    if not data.get("project_id"):
        raise ProjectError("Сначала подключите проект командой connect")
    store = vault(path)
    account, token = store.get("cloud"), store.get("bot")
    if not account or not token:
        raise ProjectError("Не найдены локальные учётные данные проекта")
    client = BotClient(data, account, token)
    # Refuse offline/unpaid operation before loading user code.
    status = await client.request("GET", "/v1/bot-projects")
    if not status.get("gateway_allowed"):
        raise ProjectError("Нужен действующий оплаченный тариф со связью между устройствами")
    await client.request(
        "POST", "/v1/bot-projects/" + data["project_id"] + "/refresh", {"token": token}
    )
    spec = importlib.util.spec_from_file_location("nanolika_owner_bot", path / "handler.py")
    if spec is None or spec.loader is None:
        raise ProjectError("Не найден обработчик бота")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not asyncio.iscoroutinefunction(getattr(module, "handle", None)):
        raise ProjectError("В handler.py нужна async def handle(update, bot)")
    inbox = LocalInbox(path / ".private/inbox.db")

    async def dispatch() -> None:
        while True:
            await client.ready.wait()
            item = inbox.next()
            if item is None:
                await asyncio.sleep(0.2)
                continue
            key, update = item
            try:
                await asyncio.wait_for(module.handle(update, client), 120)
                inbox.complete(key)
            except asyncio.CancelledError:
                raise
            except Exception:
                inbox.failed(key)
                print(
                    "Обработчик не завершил задачу; проверьте локальный проект. Содержимое сообщения и секреты в журнал не выведены.",
                    flush=True,
                )
                await asyncio.sleep(2)

    task = asyncio.create_task(dispatch())
    base = data["gateway"].rstrip("/")
    url = (
        "wss://"
        + base.removeprefix("https://")
        + "/v1/bot-projects/"
        + data["project_id"]
        + "/socket"
    )
    try:
        while True:
            try:
                async with connect(
                    url,
                    additional_headers={"Authorization": "Bearer " + account},
                    max_size=300 * 1024,
                    ping_interval=15,
                ) as ws:
                    async for raw in ws:
                        frame = json.loads(raw)
                        if frame.get("type") == "ready":
                            client.ready.set()
                            print(
                                "Бот подключён через шлюз nanoLika. Код и очередь находятся на этом компьютере.",
                                flush=True,
                            )
                        elif frame.get("type") == "update":
                            stored = False
                            try:
                                inbox.store(frame["update"])
                                stored = True
                            except (ProjectError, sqlite3.Error):
                                pass
                            await ws.send(
                                json.dumps(
                                    {"type": "ack", "delivery": frame["delivery"], "stored": stored}
                                )
                            )
            except ConnectionClosed as exc:
                if exc.code == 4004:
                    raise ProjectError(
                        "Шлюз остановлен: проверьте тариф, вход в аккаунт и состояние проекта"
                    ) from None
            except (OSError, TimeoutError, InvalidStatus):
                print("Связь временно недоступна. Повторное подключение…", flush=True)
            finally:
                client.ready.clear()
            await asyncio.sleep(3)
            await client.request(
                "POST", "/v1/bot-projects/" + data["project_id"] + "/refresh", {"token": token}
            )
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        inbox.close()


def account_key() -> str:
    value = os.environ.get("NANOMUSE_CLOUD_KEY", "").strip()
    if value:
        return value
    # The runtime already keeps the signed-in account key in its credential vault.
    from nanomuse.config import load_settings

    settings = load_settings()
    value = CredentialVault(settings.vault_file, settings.vault_key_file).get(CLOUD_KEY)
    if not value:
        raise ProjectError(
            "Войдите в nanoLika Cloud в локальном приложении; ключ нельзя присылать в чат"
        )
    return value


async def main_async(args: argparse.Namespace) -> dict | None:
    path = Path(args.path).expanduser().resolve()
    if args.action == "create":
        return create_project(path, args.platform, args.label)
    if args.action == "connect":
        return await connect_project(
            path, account_key(), getpass.getpass("Токен бота (ввод скрыт): ")
        )
    if args.action == "run":
        await run_project(path)
        return None
    data = config(path)
    store = vault(path)
    client = BotClient(data, store.get("cloud") or account_key(), store.get("bot") or "")
    if args.action == "status":
        return await client.request("GET", "/v1/bot-projects")
    return await client.request(
        "POST",
        "/v1/bot-projects/" + data["project_id"] + "/state",
        {"enabled": args.action == "resume"},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Локальные боты nanoLika через оплаченный шлюз")
    parser.add_argument("action", choices=["create", "connect", "run", "status", "pause", "resume"])
    parser.add_argument("path")
    parser.add_argument("--platform", choices=["telegram", "max"], default="telegram")
    parser.add_argument("--label", default="Мой бот")
    try:
        result = asyncio.run(main_async(parser.parse_args()))
        if result is not None:
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ProjectError, KeyboardInterrupt) as exc:
        print(
            str(exc)
            if isinstance(exc, ProjectError)
            else "Бот остановлен. Локальные данные сохранены."
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
