#!/usr/bin/env python3
"""Сторож людей: кто пришёл к Лике, кто пишет, кого пустить.

Таймер зовёт его раз в минуту. Владельцу в Телеграм уходит:

* карточка «просит разрешения» с кнопками «Впустить» / «Не пускать», когда
  незнакомец впервые пишет боту в Телеграме или MAX (ответ кнопки сторож
  исполняет сам, через API бота);
* «на сайте новый человек» — регистрация, первый вопрос, всплеск неудачных
  входов (без адресов, без переписки и без кусков чужих сообщений);
* «заход на сайт: /pricing.html, из телеграма» — по счётчику nanolika.ru;
* в 21:00 — сводка дня: заходы, люди, новые, сколько писали и сколько стоит.

    python3 people-watch.py            # один проход
    DRY=1 python3 people-watch.py      # ничего не отправлять, только напечатать
    python3 people-watch.py --digest   # прислать сводку сейчас (проверка)

Первый запуск ничего не шлёт: он только запоминает, что уже есть, чтобы не
прислать разом всю историю.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / "people-watch-state.json"
LOG = HERE / "people-watch.log"
TOKEN_FILE = HERE / "bot.token"
BOT_API_FILE = HERE / "bot-api.token"
COUNTER_KEY_FILE = HERE / "counter-admin-secret.txt"

CHAT_ID = os.environ.get("ALERT_CHAT", "224008722")
BOT = os.environ.get("BOT_API", "http://127.0.0.1:8799").rstrip("/")
DB = os.environ.get("CLOUD_DB", "/opt/nanomuse/cloud/data/cloud.db")
COUNTER = os.environ.get("COUNTER_URL", "https://nanolika.ru/counter.php")
DRY = os.environ.get("DRY") == "1"

DIGEST_AT = 21  # час по часам сервера
VISIT_LIMIT_PER_HOUR = 12
FAIL_WINDOW = 1800
FAIL_ALERT_COOLDOWN = 6 * 3600

PAGES = {
    "/": "главная",
    "/index.html": "главная",
    "/pricing.html": "цены",
    "/download.html": "скачать",
    "/inside.html": "что внутри",
    "/agent.html": "агент",
    "/models.html": "модели",
    "/tips.html": "подсказки",
    "/legal.html": "документы",
    "/privacy.html": "приватность",
    "/offer.html": "оферта",
    "/webapp/": "веб-приложение",
}
SOURCES = {
    "": "прямой заход",
    "direct": "прямой заход",
    "telegram": "из телеграма",
    "vk": "из ВК",
    "yandex": "из поиска",
    "google": "из поиска",
    "max": "из MAX",
}


# --------------------------------------------------------------------- служебное
def note(line: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        if LOG.exists() and LOG.stat().st_size > 2_000_000:
            LOG.write_text("", encoding="utf-8")
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {line}\n")
    except OSError:
        pass


def state_load() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {}


def state_save(state: dict) -> None:
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    tmp.replace(STATE)


def proxy() -> str:
    """Телеграм с этого сервера виден только через туннель к боту."""
    fixed = os.environ.get("ALERT_PROXY", "")
    if fixed:
        return fixed
    try:
        out = subprocess.run(
            ["docker", "inspect", "tunnel", "--format",
             "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}"],
            capture_output=True, text=True, timeout=20, check=False,
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return ""
    for address in out:
        if address.count(".") == 3:
            return f"http://{address}:8888"
    return ""


def telegram(method: str, payload: dict | None = None, timeout: int = 25) -> dict | None:
    token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    body = json.dumps(payload or {}).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    tunnel = proxy()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"https": tunnel}) if tunnel else urllib.request.ProxyHandler({})
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            return json.load(response)
    except Exception as exc:  # noqa: BLE001 - сторож не должен падать от сети
        note(f"телеграм {method}: {exc!r}")
        return None


def send(text: str, buttons: list[tuple[str, str]] | None = None) -> bool:
    if DRY:
        print("СООБЩЕНИЕ:", text, buttons or "")
        return True
    body: dict = {"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True}
    if buttons:
        body["reply_markup"] = {"inline_keyboard": [[{"text": title, "callback_data": data} for title, data in buttons]]}
    answer = telegram("sendMessage", body)
    return bool(answer and answer.get("ok"))


def bot_api(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    token = BOT_API_FILE.read_text(encoding="utf-8").strip()
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        f"{BOT}{path}",
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method=method,
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=25) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.load(exc)
        except ValueError:
            return exc.code, {}
    except Exception as exc:  # noqa: BLE001
        note(f"бот {method} {path}: {exc!r}")
        return 0, {}


def cloud_db() -> sqlite3.Connection | None:
    try:
        return sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error as exc:
        note(f"база не открылась: {exc!r}")
        return None


def day_start(moment: datetime | None = None) -> float:
    moment = moment or datetime.now()
    return moment.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


# --------------------------------------------------------------------- разрешения
def check_approvals(state: dict) -> None:
    status, view = bot_api("GET", "/api/channels")
    if status != 200:
        return
    announced = state.setdefault("announced", {})
    for item in view.get("pending") or []:
        code = str(item.get("code") or "")
        if not code or code in announced:
            continue
        who = item.get("sender_name") or item.get("sender_id") or "незнакомец"
        channel = item.get("channel") or "?"
        try:
            until = datetime.fromtimestamp(float(item.get("expires_at") or 0)).strftime("%H:%M")
        except (TypeError, ValueError):
            until = "?"
        text = (
            f"Просит разрешения: {who} · {channel}\n"
            f"Код {code}, действует до {until}.\n"
            "Впустить — кнопкой ниже или ответом «да " + code + "»."
        )
        if send(text, [("Впустить", f"allow:{code}"), ("Не пускать", f"deny:{code}")]):
            announced[code] = time.time()
            note(f"прислал карточку разрешения {code} ({who}, {channel})")
    cutoff = time.time() - 86400
    for code in [c for c, ts in announced.items() if float(ts) < cutoff]:
        announced.pop(code, None)


def handle_updates(state: dict) -> None:
    """Кнопки и ответы владельца: впустить или не пустить."""
    offset = int(state.get("update_offset") or 0)
    answer = telegram("getUpdates", {"offset": offset, "timeout": 0, "limit": 50,
                                     "allowed_updates": ["callback_query", "message"]})
    if not answer or not answer.get("ok"):
        return
    for update in answer.get("result") or []:
        offset = max(offset, int(update.get("update_id") or 0) + 1)
        if "callback_query" in update:
            query = update["callback_query"]
            data = str(query.get("data") or "")
            message = query.get("message") or {}
            message_id = message.get("message_id")
            action, _, code = data.partition(":")
            if action not in ("allow", "deny") or not code:
                continue
            verb = "впустила" if action == "allow" else "не пустила"
            status, result = bot_api(
                "POST", f"/api/channels/pairing/{code}/{'approve' if action == 'allow' else 'deny'}"
            )
            done = status == 200
            note(f"решение по {code}: {action} -> {status}")
            if message_id:
                text = (f"{verb.capitalize()} {code}." if done
                        else f"Не вышло: код {code} уже не действует.")
                telegram("editMessageText", {"chat_id": message.get("chat", {}).get("id") or CHAT_ID,
                                             "message_id": message_id, "text": text})
                telegram("answerCallbackQuery", {"callback_query_id": query.get("id"),
                                                "text": "Готово" if done else "Не вышло"})
        elif "message" in update:
            message = update["message"]
            text = str(message.get("text") or "").strip()
            chat = str((message.get("chat") or {}).get("id") or "")
            if chat != str(CHAT_ID):
                continue
            parts = text.replace("ё", "е").lower().split()
            if len(parts) < 2 or parts[0] not in ("да", "нет", "yes", "no"):
                continue
            code = parts[1].upper()
            allow = parts[0] in ("да", "yes")
            status, _ = bot_api(
                "POST", f"/api/channels/pairing/{code}/{'approve' if allow else 'deny'}"
            )
            note(f"решение словом по {code}: {parts[0]} -> {status}")
            if status == 200:
                send(f"Готово: {code} — {'впустила' if allow else 'не пустила'}.")
            else:
                send(f"Код {code} уже не действует.")
    state["update_offset"] = offset


# ------------------------------------------------------------------------- люди
def founder_ids(connection: sqlite3.Connection) -> set[str]:
    """Владелец и тестовые аккаунты: их не считаем «людьми» и о них не пишем."""
    out = set()
    for account_id, unlimited, is_test in connection.execute(
        "select id, unlimited, is_test from accounts"
    ):
        if unlimited or is_test:
            out.add(account_id)
    return out


def check_new_people(state: dict, connection: sqlite3.Connection) -> None:
    skip = founder_ids(connection)
    last_id = int(state.get("event_id") or 0)
    rows = list(connection.execute(
        "select e.id, e.ts, e.account_id, e.kind, e.detail, a.hint, a.channel, a.last_ua "
        "from events e left join accounts a on a.id = e.account_id "
        "where e.id > ? order by e.id",
        (last_id,),
    ))
    for event_id, ts, account_id, kind, detail, hint, channel, ua in rows:
        last_id = max(last_id, int(event_id))
        if kind in ("account.created",) and account_id not in skip:
            device = device_of(ua or "")
            text = f"Новый человек: {hint or 'без имени'} · {channel or '?'}"
            if device:
                text += f" · {device}"
            send(text)
            note(f"новый аккаунт {account_id} {hint}")
    state["event_id"] = last_id


def device_of(ua: str) -> str:
    low = ua.lower()
    for needle, name in (("iphone", "iPhone"), ("ipad", "iPad"), ("android", "Android"),
                         ("windows", "Windows"), ("macintosh", "Mac")):
        if needle in low:
            return name
    return ""


def check_first_messages(state: dict, connection: sqlite3.Connection) -> None:
    skip = founder_ids(connection)
    told = set(state.get("told_chats") or [])
    week_ago = time.time() - 7 * 86400
    rows = connection.execute(
        "select l.account_id, min(l.ts) as first, count(*) as said, a.hint, a.is_test, a.unlimited "
        "from ledger l join accounts a on a.id = l.account_id "
        "where l.kind in ('chat', 'image', 'video') and a.created_at > ? "
        "group by l.account_id",
        (week_ago,),
    )
    for account_id, first, said, hint, is_test, unlimited in rows:
        if account_id in skip or account_id in told or is_test or unlimited:
            continue
        send(f"Написала впервые: {hint or 'без почты'} — {said} обращений.")
        told.add(account_id)
        note(f"первая переписка {account_id} {hint} ({said})")
    state["told_chats"] = sorted(told)


def check_sign_in_trouble(state: dict, connection: sqlite3.Connection) -> None:
    skip = founder_ids(connection)
    told = state.setdefault("fail_alerts", {})
    since = time.time() - FAIL_WINDOW
    rows = connection.execute(
        "select e.account_id, count(*), max(e.ts), a.hint from events e "
        "left join accounts a on a.id = e.account_id "
        "where e.kind = 'sign_in.failed' and e.ts > ? group by e.account_id",
        (since,),
    ).fetchall()
    for account_id, count, last, hint in rows:
        if not account_id or account_id in skip or count < 3:
            continue
        if time.time() - float(told.get(account_id) or 0) < FAIL_ALERT_COOLDOWN:
            continue
        when = datetime.fromtimestamp(float(last)).strftime("%H:%M")
        send(f"Не может войти: {hint or 'без почты'} — {count} попытки, последняя в {when}.")
        told[account_id] = time.time()
        note(f"неудачные входы {account_id} x{count}")


# ------------------------------------------------------------------------- сайт
ADMIN_TOKEN_FILE = HERE / "admin-access.txt"
RELAY = os.environ.get("RELAY", "http://127.0.0.1:8790").rstrip("/")


def site_visits() -> list[dict]:
    """Журнал посещений — из админки релея, если есть токен, иначе у счётчика.

    Релей уже складывает туда же регион каждого адреса (geo.py, ip2region на диске),
    поэтому из админки заход приходит с местом: «Краснодар». Без токена работает
    прямая выгрузка со счётчика — без региона.
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    if ADMIN_TOKEN_FILE.exists():
        token = ADMIN_TOKEN_FILE.read_text(encoding="utf-8").strip()
        request = urllib.request.Request(
            f"{RELAY}/v1/admin/site-counter",
            headers={"X-Admin-Token": token},
        )
        try:
            with opener.open(request, timeout=25) as response:
                data = json.load(response)
        except Exception as exc:  # noqa: BLE001
            note(f"админка релея не ответила: {exc!r}")
        else:
            if isinstance(data, dict) and data.get("details_available"):
                return [row for row in data.get("visits") or [] if isinstance(row, dict)]
            return []
    if not COUNTER_KEY_FILE.exists():
        return []
    key = COUNTER_KEY_FILE.read_text(encoding="utf-8").strip()
    request = urllib.request.Request(
        f"{COUNTER}?details=1",
        headers={"X-Counter-Key": key, "User-Agent": "nanolika-people-watch/1.0"},
    )
    try:
        with opener.open(request, timeout=25) as response:
            data = json.load(response)
    except Exception as exc:  # noqa: BLE001
        note(f"счётчик не ответил: {exc!r}")
        return []
    if not data.get("ok"):
        return []
    return [row for row in data.get("recent") or [] if isinstance(row, dict)]


def check_visits(state: dict) -> None:
    visits = site_visits()
    if not visits:
        return
    last_id = int(state.get("visit_id") or 0)
    fresh = [event for event in visits if int(event.get("id") or 0) > last_id]
    if not fresh:
        return
    state["visit_id"] = max(int(event.get("id") or 0) for event in fresh)
    stamps = [ts for ts in state.get("visit_sent") or [] if float(ts) > time.time() - 3600]
    for event in fresh:
        if not event.get("new"):
            continue
        if len(stamps) >= VISIT_LIMIT_PER_HOUR:
            break
        page = PAGES.get(str(event.get("page") or ""), str(event.get("page") or "?"))
        source = SOURCES.get(str(event.get("source") or ""), str(event.get("source") or ""))
        device = device_of(str(event.get("ua") or ""))
        place = str(event.get("place") or "").strip()
        if place:
            # «Russia · Krasnodar Krai · Krasnodar» → «Krasnodar»: в сообщении нужен город,
            # страна и область только удлиняют строку; целиком это видно в админке.
            place = place.split(" · ")[-1]
        when = datetime.fromtimestamp(float(event.get("at") or 0)).strftime("%H:%M")
        line = f"Заход на сайт · {when} · {page}"
        if source:
            line += f" · {source}"
        if place:
            line += f" · {place}"
        elif device:
            line += f" · {device}"
        if send(line):
            stamps.append(time.time())
            note(f"заход {event.get('id')} {page} {source} {place}")
    state["visit_sent"] = stamps


def counter_stats() -> dict:
    try:
        with urllib.request.urlopen(f"{COUNTER}?stats=1", timeout=20) as response:
            return json.load(response)
    except Exception as exc:  # noqa: BLE001
        note(f"сводка счётчика: {exc!r}")
        return {}


def rubles(uy: int, connection: sqlite3.Connection | None) -> float:
    """Условные единицы в рубли — по курсам из настроек релея."""
    per_usd, usd_cny = 100.0, 7.1
    if connection is not None:
        try:
            rows = dict(connection.execute(
                "select key, value from settings where key in ('rub_per_usd', 'usd_cny')"
            ).fetchall())
            per_usd = float(json.loads(rows.get("rub_per_usd") or "100"))
            usd_cny = float(json.loads(rows.get("usd_cny") or "7.1"))
        except (sqlite3.Error, ValueError, TypeError):
            pass
    return uy / 1_000_000 * (per_usd / usd_cny)


def digest(connection: sqlite3.Connection | None) -> str:
    stats = counter_stats()
    views = int(stats.get("today") or 0)
    people = int(stats.get("uniq_today") or 0)
    total = int(stats.get("total") or 0)
    new_people = said_people = messages = 0
    spent = 0.0
    if connection is not None:
        skip = founder_ids(connection)
        start = day_start()
        marks = ",".join("?" * len(skip)) or "''"
        new_people = connection.execute(
            "select count(*) from events where kind = 'account.created' and ts >= ?", (start,)
        ).fetchone()[0]
        said_people, messages = connection.execute(
            f"select count(distinct account_id), count(*) from ledger "
            f"where kind in ('chat', 'image', 'video') and ts >= ? and account_id not in ({marks})",
            (start, *skip),
        ).fetchone()
        spent = rubles(int(connection.execute(
            f"select coalesce(sum(sale_uy), 0) from ledger where ts >= ? and account_id not in ({marks})",
            (start, *skip),
        ).fetchone()[0] or 0), connection)
    lines = [
        f"Итоги дня · {datetime.now().strftime('%d.%m')}",
        f"Сайт: {views} просмотров, {people} человек (за всё время {total}).",
        f"Новых аккаунтов: {new_people}.",
        f"Писали Лике: {said_people} чел., {messages} обращений.",
        f"Стоили они: {spent:.2f} ₽ (без твоих чатов).",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------- главное
def main() -> int:
    state = state_load()
    first_run = not state.get("started")
    if first_run:
        connection = cloud_db()
        if connection is not None:
            state["event_id"] = connection.execute("select coalesce(max(id), 0) from events").fetchone()[0]
            state["told_chats"] = [
                row[0] for row in connection.execute(
                    "select distinct account_id from ledger where kind in ('chat','image','video')"
                )
            ]
            connection.close()
        if COUNTER_KEY_FILE.exists():
            check_visits(state)  # только запоминает последний id
        state["started"] = time.time()
        state_save(state)
        note("первый запуск: состояние запомнено, ничего не отправлено")
        if "--digest" not in sys.argv:
            return 0

    connection = cloud_db()
    try:
        handle_updates(state)
        check_approvals(state)
        if connection is not None:
            check_new_people(state, connection)
            check_first_messages(state, connection)
            check_sign_in_trouble(state, connection)
        check_visits(state)
        now = datetime.now()
        today = date.today().isoformat()
        if now.hour >= DIGEST_AT and state.get("digest_day") != today:
            message = digest(connection)
            if send(message):
                state["digest_day"] = today
                note("сводка дня отправлена")
        if "--digest" in sys.argv and state.get("digest_day") != today:
            if send(digest(connection)):
                state["digest_day"] = today
    finally:
        if connection is not None:
            connection.close()
        state_save(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
