#!/usr/bin/env python3
"""Балансы всех поставщиков моделей: одна строка на поставщика, один алерт, когда пусто у всех.

Раз в пять минут смотрит прямые ключи из /opt/nanomuse/cloud/.env и ключи
каналов шлюза new-api. Пока хотя бы у одного поставщика есть деньги — молчит.
Когда не осталось ни у кого — присылает одну сводку со всеми поставщиками.
Одно и то же не чаще раза в полчаса.

    python3 provider-balance.py            # обычная проверка
    DRY=1 python3 provider-balance.py      # только напечатать
    python3 provider-balance.py --test     # прислать сводку сейчас, даже если всё хорошо
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / "balance-state.json"
LOG = HERE / "balance.log"
CHAT_ID = "224008722"
DRY = os.environ.get("DRY") == "1"
BOT_TOKEN_FILE = HERE / "bot.token"

# Порог «мало» только для пометки в сводке: тревога — когда пусто у всех.
LOW = {"RUB": 100.0, "CNY": 5.0, "USD": 1.0}
COOLDOWN = timedelta(minutes=30)
ENV_FILES = (Path("/opt/nanomuse/cloud/.env"), Path("/opt/ai-bridge/.env"))
CHANNEL_DB = Path("/opt/ai-bridge/data/one-api.db")

# Прямые ключи. `public` — то, что человек узнаёт; `base` — куда стучимся.
PROVIDERS = [
    {
        "id": "openrouter",
        "title": "openrouter",
        "public": "openrouter.ai",
        "base": "http://127.0.0.1:9443/api",
        "path": "/v1/credits",
        "key_env": "OPENROUTER_KEY",
        "kind": "openrouter",
    },
    {
        "id": "deepseek",
        "title": "deepseek",
        "public": "api.deepseek.com",
        "base": "https://api.deepseek.com",
        "path": "/user/balance",
        "key_env": "DEEPSEEK_KEY",
        "kind": "deepseek",
    },
    {
        "id": "polza",
        "title": "polza",
        "public": "api.polza.ai",
        "base": "https://api.polza.ai",
        "path": "/api/v1/balance",
        "key_env": "POLZA_KEY",
        "kind": "polza",
    },
    {
        "id": "routerai",
        "title": "routerai",
        "public": "routerai.ru",
        "base": "https://routerai.ru",
        "path": "/api/v1/credits",
        "key_env": "ROUTERAI_KEY",
        "kind": "routerai",
    },
    {
        "id": "proxyapi",
        "title": "proxyapi",
        "public": "api.proxyapi.ru",
        "base": "https://api.proxyapi.ru",
        "path": "/proxyapi/balance",
        "key_env": "PROXYAPI_KEY",
        "kind": "proxyapi",
    },
    {
        "id": "aitunnel",
        "title": "aitunnel",
        "public": "api.aitunnel.ru",
        "base": "https://api.aitunnel.ru",
        "path": "/v1/aitunnel/balance",
        "key_env": "AITUNNEL_KEY",
        "kind": "aitunnel",
    },
    {
        "id": "bothub",
        "title": "bothub",
        "public": "bothub.chat",
        "base": "https://bothub.chat/api/v2/openai/v1",
        "path": "/models",
        "key_env": "BOTHUB_KEY",
        "kind": "bothub",
    },
]


@dataclass
class KeyRow:
    label: str
    amounts: list[tuple[float, str]] = field(default_factory=list)
    error: str = ""
    no_balance: bool = False


@dataclass
class ProviderRow:
    title: str
    public: str
    keys: list[KeyRow] = field(default_factory=list)

    @property
    def status(self) -> str:
        states = {key_status(row) for row in self.keys}
        for name in ("есть", "мало", "пусто"):
            if name in states:
                return name
        if "нет API" in states:
            return "нет API"
        return "нет данных"


def note(line: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {line}\n")


def alert_proxy() -> str:
    fixed = os.environ.get("ALERT_PROXY", "")
    if fixed:
        return fixed
    try:
        output = subprocess.run(
            ["docker", "inspect", "tunnel", "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return ""
    for address in output:
        if address.count(".") == 3:
            return f"http://{address}:8888"
    return ""


def send(text: str) -> bool:
    if DRY:
        print("СООБЩЕНИЕ:", text.replace("\n", " | "))
        return True
    body = json.dumps({"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True}).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN_FILE.read_text(encoding='utf-8').strip()}/sendMessage",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    proxy = alert_proxy()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"https": proxy}) if proxy else urllib.request.ProxyHandler({})
    )
    try:
        with opener.open(request, timeout=25) as answer:
            return bool(json.load(answer).get("ok"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        note(f"не смогла отправить: {exc}")
        return False


def env_file() -> dict[str, str]:
    values: dict[str, str] = {}
    for path in ENV_FILES:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:]
            key, value = line.split("=", 1)
            values.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return values


def number(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def fetch(kind: str, base: str, path: str, key: str) -> tuple[dict | None, str]:
    url = base.rstrip("/") + path
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as answer:
            raw = answer.read()
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError) as exc:
        return None, type(exc).__name__
    try:
        data = json.loads(raw)
    except ValueError:
        return None, "ответ не JSON"
    if not isinstance(data, dict):
        return None, "ответ не объект"
    return data, ""


def parse(kind: str, data: dict) -> list[tuple[float, str]]:
    if kind == "polza":
        amount = number(data.get("available"))
        if amount is None:
            amount = number(data.get("amount"))
        return [(amount, "CNY")] if amount is not None else []
    if kind == "routerai":
        amount = number((data.get("data") or {}).get("credits"))
        return [(amount, "RUB")] if amount is not None else []
    if kind == "proxyapi":
        amount = number(data.get("balance"))
        return [(amount, "RUB")] if amount is not None else []
    if kind == "aitunnel":
        amount = number(data.get("balance"))
        budget = number(data.get("budget"))
        if amount is None:
            amount = budget
        elif budget is not None:
            amount = min(amount, budget)
        return [(amount, "RUB")] if amount is not None else []
    if kind == "openrouter":
        body = data.get("data") or {}
        total = number(body.get("total_credits"))
        used = number(body.get("total_usage"))
        if total is None or used is None:
            return []
        return [(total - used, "USD")]
    if kind == "deepseek":
        found: list[tuple[float, str]] = []
        for row in data.get("balance_infos") or []:
            currency = str(row.get("currency") or "").upper()
            amount = number(row.get("total_balance"))
            if currency and amount is not None:
                found.append((amount, currency))
        return found
    return []


def key_status(row: KeyRow) -> str:
    if row.error:
        return "нет данных"
    if row.no_balance:
        return "нет API"
    positives = [(amount, currency) for amount, currency in row.amounts if amount > 0]
    if not positives:
        return "пусто"
    if any(amount > LOW.get(currency, 0) for amount, currency in positives):
        return "есть"
    return "мало"


def provider_of_base(base: str) -> str:
    lowered = base.lower()
    if "deepseek.com" in lowered:
        return "deepseek"
    if "polza.ai" in lowered:
        return "polza"
    if "routerai.ru" in lowered:
        return "routerai"
    if "proxyapi.ru" in lowered:
        return "proxyapi"
    if "aitunnel.ru" in lowered:
        return "aitunnel"
    if "openrouter.ai" in lowered:
        return "openrouter"
    if "bothub.chat" in lowered:
        return "bothub"
    return ""


def channel_keys() -> list[tuple[str, str, str]]:
    """(provider id, label, key) из включённых каналов шлюза new-api."""
    if not CHANNEL_DB.exists():
        return []
    found: list[tuple[str, str, str]] = []
    database = None
    try:
        database = sqlite3.connect(f"file:{CHANNEL_DB}?mode=ro", uri=True)
        for name, base, key in database.execute(
            "SELECT name, base_url, key FROM channels WHERE status=1"
        ):
            provider = provider_of_base(str(base or ""))
            if provider and key:
                found.append((provider, f"шлюз {name}", str(key)))
    except sqlite3.Error as exc:
        note(f"база шлюза: {exc}")
    finally:
        if database is not None:
            database.close()
    return found


def collect(env: dict[str, str]) -> list[ProviderRow]:
    providers: dict[str, ProviderRow] = {
        item["id"]: ProviderRow(item["title"], item["public"]) for item in PROVIDERS
    }
    keys: dict[str, list[tuple[str, str]]] = {}

    for item in PROVIDERS:
        key = env.get(item["key_env"], "").strip()
        if key:
            keys.setdefault(item["id"], []).append(("прямой", key))
    for provider, label, key in channel_keys():
        if provider in providers:
            keys.setdefault(provider, []).append((label, key))

    for item in PROVIDERS:
        rows = keys.get(item["id"]) or []
        seen: set[str] = set()
        for label, key in rows:
            if key in seen:
                continue
            seen.add(key)
            data, error = fetch(item["kind"], item["base"], item["path"], key)
            row = KeyRow(label=label)
            if error:
                row.error = error
            elif item["kind"] == "bothub":
                # Bothub отдаёт каталог моделей, но не баланс: ключ проверить можно,
                # деньги — нет. Так и говорим, чтобы не показывать выдуманный ноль.
                row.no_balance = True
            else:
                row.amounts = parse(item["kind"], data or {})
                if not row.amounts:
                    row.error = "не разобрать баланс"
            providers[item["id"]].keys.append(row)
    return [row for row in providers.values() if row.keys]


def money(amount: float, currency: str) -> str:
    if currency == "RUB":
        return f"{amount:.2f} ₽"
    if currency == "USD":
        return f"${amount:.2f}"
    if currency == "CNY":
        return f"{amount:.2f} CNY"
    return f"{amount:.2f} {currency}"


def amounts_text(row: KeyRow) -> str:
    if row.error:
        return row.error
    if row.no_balance:
        return "ключ рабочий, баланс не отдаёт API"
    # Сначала то, на что можно жить, потом нули и минусы; валюты — в постоянном порядке.
    order = {"USD": 0, "CNY": 1, "RUB": 2}
    amounts = sorted(row.amounts, key=lambda item: (item[0] <= 0, order.get(item[1], 9)))
    return ", ".join(money(amount, currency) for amount, currency in amounts)


def render(rows: list[ProviderRow]) -> str:
    lines: list[str] = []
    for provider in rows:
        status = provider.status
        if len(provider.keys) == 1:
            row = provider.keys[0]
            if row.no_balance:
                lines.append(f"{provider.title} ({provider.public}): ключ рабочий, баланс не отдаёт API")
            else:
                lines.append(f"{provider.title} ({provider.public}): {amounts_text(row)} — {status}")
            continue
        lines.append(f"{provider.title} ({provider.public}) — {status}")
        for row in provider.keys:
            lines.append(f"  {row.label}: {amounts_text(row)} — {key_status(row)}")
    return "\n".join(lines)


def state() -> dict:
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def main() -> int:
    env = env_file()
    rows = collect(env)
    if not rows:
        print("нечего проверять: нет ключей поставщиков")
        return 0

    summary = render(rows)
    usable = [row for row in rows if row.status in ("есть", "мало")]
    all_empty = not usable
    print(summary)
    note(f"проверено {len(rows)}, с деньгами {len(usable)}")

    if "--test" in sys.argv:
        send("Сводка по деньгам поставщиков\n\n" + summary)
        return 0

    saved = state()
    now = datetime.now()
    if all_empty:
        last = saved.get("all_empty_at")
        quiet = False
        if last:
            try:
                quiet = now - datetime.fromisoformat(last) < COOLDOWN
            except ValueError:
                quiet = False
        if not quiet and send("Деньги кончились у всех поставщиков\n\n" + summary):
            saved["all_empty_at"] = now.isoformat(timespec="seconds")
        saved["all_empty"] = True
    else:
        if saved.get("all_empty"):
            send("Деньги снова есть у поставщиков\n\n" + summary)
        saved["all_empty"] = False
        saved.pop("all_empty_at", None)
    STATE.write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
