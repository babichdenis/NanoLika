"""Кто заходил: по адресу, браузеру и странице, с которой пришли.

Читает access-лог Caddy (`/data/access.json` внутри контейнера) и сводит запросы в
список людей: сколько раз, какие страницы, каким браузером, откуда перешли, когда
последний раз. «Прямой» значит, что браузер не сказал, откуда пришёл: закладка,
ввод адреса, переход из приложения.

    python3 scripts/who.py            # за сутки, все пути
    python3 scripts/who.py 6 /app/    # за 6 часов, только админка
"""
import collections
import json
import subprocess
import sys
from datetime import datetime, timezone

HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 24.0
ONLY = sys.argv[2] if len(sys.argv) > 2 else ""
STREAMS = ("http.log.access.log0", "http.log.access")

raw = subprocess.run(
    ["docker", "exec", "cloud-caddy-1", "tail", "-n", "300000", "/data/access.json"],
    capture_output=True,
    text=True,
).stdout

people = collections.defaultdict(
    lambda: {"hits": 0, "pages": collections.Counter(), "from": collections.Counter(), "last": 0.0, "ua": ""}
)
now = datetime.now(timezone.utc).timestamp()
for line in raw.splitlines():
    try:
        rec = json.loads(line)
    except ValueError:
        continue
    if rec.get("logger") not in STREAMS:
        continue
    if now - float(rec.get("ts") or 0) > HOURS * 3600:
        continue
    req = rec.get("request") or {}
    uri = str(req.get("uri") or "")
    if ONLY and ONLY not in uri:
        continue
    ip = str(req.get("remote_ip") or "")
    if not ip or int(rec.get("status") or 0) >= 500:
        continue
    headers = req.get("headers") or {}
    ua = str((headers.get("User-Agent") or ["—"])[0])[:64]
    ref = str((headers.get("Referer") or ["прямой"])[0]).split("?")[0][:70]
    row = people[ip]
    row["hits"] += 1
    row["pages"][uri.split("?")[0][:44]] += 1
    row["from"][ref] += 1
    row["ua"] = ua
    row["last"] = max(row["last"], float(rec.get("ts") or 0))

if not people:
    print(f"за {HOURS:g} ч записей нет")
    sys.exit()

print(f"за {HOURS:g} ч, путей с «{ONLY or 'любыми'}»: {len(people)} адресов\n")
for ip, row in sorted(people.items(), key=lambda kv: -kv[1]["last"]):
    when = datetime.fromtimestamp(row["last"]).strftime("%d.%m %H:%M")
    pages = ", ".join(f"{p} x{n}" for p, n in row["pages"].most_common(3))
    src = ", ".join(f"{r} x{n}" for r, n in row["from"].most_common(2))
    hits = row["hits"]
    print(f"{ip:16} {hits:5} запр.   последний {when}")
    print(f"    браузер: {row['ua']}")
    print(f"    страницы: {pages}")
    print(f"    откуда: {src}")
