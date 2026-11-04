#!/usr/bin/env python3
"""Сторож за тремя Ликами: веб-чат на сайте, Телеграм и MAX.

Раз в пять минут проверяет, что каждая отвечает, и пишет в Телеграм, если нет.
Когда проверка снова проходит, присылает короткое «поднялась», чтобы не гадать.

    python3 lika-watch.py            # обычная проверка
    python3 lika-watch.py --test     # прислать сводку о состоянии сейчас
    DRY=1 python3 lika-watch.py      # ничего не отправлять, только напечатать

Адреса можно подменить переменными окружения (WEB_URL, BOT_API) — это нужно
для проверки самого сторожа: подставил заведомо мёртвый адрес и убедился, что
он заметил.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / "lika-watch-state.json"
TOKEN_FILE = HERE / "bot.token"
LOG = HERE / "lika-watch.log"
CHAT_ID = "224008722"

WEB = os.environ.get("WEB_URL", "https://chat.nanolika.ru").rstrip("/")
BOT = os.environ.get("BOT_API", "http://127.0.0.1:8799").rstrip("/")
DRY = os.environ.get("DRY") == "1"

COOLDOWN = timedelta(minutes=30)
CANARY = "Привет! Ответь одной короткой фразой: ты на связи?"
CONTAINERS = {"chat": "Веб-чат", "nanolika-bot": "Бот", "tunnel": "Туннель к Телеграму"}
# Диск: new-api при 95% занятости перестаёт отвечать вообще («system disk
# overloaded»), и тогда молчат все три Лики разом. 04.11 так и вышло: 99%, четыре
# часа тишины. Поэтому про диск предупреждаем заранее, а не задним числом.
DISK_WARN = float(os.environ.get("DISK_WARN", "90"))
DISK_EMERGENCY = float(os.environ.get("DISK_EMERGENCY", "94"))


def note(line: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {line}\n")


def alert_proxy() -> str:
    """С этого сервера Телеграм доступен только через туннель: беру адрес его контейнера."""
    fixed = os.environ.get("ALERT_PROXY", "")
    if fixed:
        return fixed
    try:
        output = subprocess.run(
            ["docker", "inspect", "tunnel", "--format",
             "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}"],
            capture_output=True, text=True, timeout=20, check=False,
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return ""
    # первая сеть контейнера: туннель слушает 8888 на всех интерфейсах
    for address in output:
        if address.count(".") == 3:
            return f"http://{address}:8888"
    return ""


def send(text: str) -> bool:
    if DRY:
        print("СООБЩЕНИЕ:", text)
        return True
    body = json.dumps({"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True}).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN_FILE.read_text(encoding='utf-8').strip()}/sendMessage",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    proxy = alert_proxy()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"https": proxy}) if proxy else urllib.request.ProxyHandler({})
    )
    try:
        with opener.open(request, timeout=25) as response:
            return bool(json.load(response).get("ok"))
    except Exception as exc:  # noqa: BLE001 - сторож не должен падать от сети
        note(f"не смог отправить сообщение: {exc!r}")
        return False


def http(url: str, payload: dict | None = None, timeout: int = 25, token: str = "") -> tuple[int, str]:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json", "Origin": "https://nanolika.ru"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read().decode("utf-8", "replace")


def bot_token() -> str:
    cached = HERE / "bot-api.token"
    if cached.exists() and cached.read_text(encoding="utf-8").strip():
        return cached.read_text(encoding="utf-8").strip()
    try:
        output = subprocess.run(
            ["docker", "logs", "nanolika-bot"],
            capture_output=True, text=True, timeout=60, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    import re

    found = re.findall(r"token=([A-Za-z0-9]{10,})", output)
    if not found:
        return ""
    token = found[-1]
    cached.write_text(token, encoding="utf-8")
    cached.chmod(0o600)
    return token


def check_web() -> str | None:
    """None — всё хорошо, строка — что не так."""
    try:
        status, _ = http(f"{WEB}/healthz", timeout=15)
        if status != 200:
            return f"healthz отдаёт {status}"
    except Exception as exc:  # noqa: BLE001
        return f"не отвечает на healthz: {type(exc).__name__}"
    started = time.monotonic()
    try:
        status, text = http(f"{WEB}/chat", {"messages": [{"role": "user", "content": CANARY}]}, timeout=45)
    except urllib.error.HTTPError as exc:
        # Релей отвечает телом с кодом отказа — он и есть причина: «нет денег»,
        # «диск переполнен», «модель не отвечает». Без него в логе было просто
        # «HTTPError», и четыре часа тишины никто не понял.
        return f"не ответила: {refusal_text(exc)}"
    except Exception as exc:  # noqa: BLE001
        return f"не ответила на вопрос: {type(exc).__name__}"
    spent = time.monotonic() - started
    if status != 200:
        return f"на вопрос ответила кодом {status}"
    try:
        reply = str(json.loads(text).get("reply") or "").strip()
    except ValueError:
        return "ответ не разобрать как JSON"
    if not reply:
        return "ответила пустотой"
    if spent > 30:
        return f"думала {spent:.0f} секунд"
    return None


def check_channel(name: str) -> str | None:
    token = bot_token()
    if not token:
        return "не нашла ключ API бота"
    try:
        status, text = http(f"{BOT}/api/channels", timeout=15, token=token)
    except urllib.error.HTTPError as exc:
        return f"API бота ответил {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return f"API бота недоступен: {type(exc).__name__}"
    if status != 200:
        return f"API бота ответил {status}"
    try:
        channels = json.loads(text).get("channels") or []
    except ValueError:
        return "список каналов не разобрать"
    for channel in channels:
        if channel.get("name") != name:
            continue
        state = str((channel.get("status") or {}).get("state") or "?")
        if state != "connected":
            detail = str((channel.get("status") or {}).get("detail") or "")[:80]
            return f"канал в состоянии {state} {detail}".strip()
        return None
    return "канала нет в списке"


def check_container(name: str) -> str | None:
    try:
        output = subprocess.run(
            ["docker", "inspect", name, "--format", "{{.State.Status}}"],
            capture_output=True, text=True, timeout=20, check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return f"не смогла спросить докер: {type(exc).__name__}"
    if output != "running":
        return f"контейнер в состоянии {output or 'нет ответа'}"
    return None


def refusal_text(exc: urllib.error.HTTPError) -> str:
    """Код и слова отказа из тела ответа: их понимает человек, а не только я."""
    try:
        body = json.loads(exc.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return f"код {exc.code}"
    code = str(body.get("code") or "").strip()
    words = str(body.get("reply") or body.get("error") or "").strip().replace("\n", " ")
    parts = [f"код {exc.code}"]
    if code:
        parts.append(code)
    if words:
        parts.append(words[:120])
    return " · ".join(parts)


def check_disk() -> str | None:
    """Место на диске: при 95% new-api отказывает всем, и молчат все три Лики."""
    try:
        usage = shutil.disk_usage("/")
    except OSError as exc:
        return f"не смогла посмотреть диск: {type(exc).__name__}"
    percent = usage.used / usage.total * 100
    free = usage.free / 1024 ** 3
    if percent >= DISK_EMERGENCY:
        return (f"диск занят {percent:.0f}%, свободно {free:.1f} ГБ — при 95% "
                "new-api перестаёт отвечать и молчат все Лики")
    if percent >= DISK_WARN:
        return f"диск занят {percent:.0f}%, свободно {free:.1f} ГБ — пора чистить"
    return None


def checks() -> dict[str, callable]:
    found: dict[str, callable] = {"Веб-Лика": check_web, "Телеграм-Лика": lambda: check_channel("telegram"),
                                  "MAX-Лика": lambda: check_channel("max"),
                                  "Диск": check_disk}
    for name, title in CONTAINERS.items():
        found[title] = (lambda n=name: check_container(n))
    return found


def main() -> int:
    state = {}
    if STATE.exists():
        try:
            state = json.loads(STATE.read_text(encoding="utf-8"))
        except ValueError:
            state = {}
    now = datetime.now()
    problems: list[str] = []
    recoveries: list[str] = []
    for title, check in checks().items():
        try:
            problem = check()
        except Exception as exc:  # noqa: BLE001 - сторож обязан пережить любую ошибку
            problem = f"проверка сломалась: {type(exc).__name__}"
        entry = state.get(title) or {}
        if problem:
            problems.append(f"{title}: {problem}")
            last = entry.get("sent_at")
            quiet = False
            if last:
                try:
                    quiet = now - datetime.fromisoformat(last) < COOLDOWN
                except ValueError:
                    quiet = False
            if not quiet and not DRY:
                if send(f"{title}: {problem}"):
                    entry["sent_at"] = now.isoformat(timespec="seconds")
            entry["failing"] = True
        else:
            if entry.get("failing"):
                recoveries.append(title)
            entry["failing"] = False
        state[title] = entry
        note(f"{title}: {'ok' if not problem else problem}")
    if recoveries and not DRY:
        send("Снова в порядке: " + ", ".join(recoveries))
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    if "--test" in sys.argv:
        text = ("Всё в порядке: " + ", ".join(checks()) if not problems
                else "Проблемы: " + "; ".join(problems))
        send("Сторож за Ликами на связи. " + text)
    print("\n".join(problems) if problems else "все три Лики и сервисы отвечают")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
