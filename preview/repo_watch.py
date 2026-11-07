#!/usr/bin/env python3
"""Превью-наблюдатель: следит за github.com/nano-muse/nanoMuse и сравнивает с нашим.

Только превью — живую ленту (nanomuse/server/service.py: feed_posts/write_feed_posts)
НЕ меняет. Показывает, ЧТО можно класть в ленту. Запуск:
    python preview/repo_watch.py [--json]

Для продакшна результат подключается как источник в _feed_context() ленты.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import urllib.request
from datetime import datetime, timezone

UPSTREAM = "nano-muse/nanoMuse"
OURS_REMOTE = "origin"          # наш git remote (для ветки/HEAD)
OURS_DIR = "/Users/babichdenis/Dev/nanoMuse"   # наш локальный клон


def _git(dir_: str, *args: str) -> str:
    return subprocess.check_output(["git", "-C", dir_, *args]).decode().strip()


def ours_head() -> dict:
    rev = _git(OURS_DIR, "log", "-1", "--format=%h %ad %s", "--date=short")
    branch = _git(OURS_DIR, "branch", "--show-current")
    h, rest = rev.split(" ", 1)
    date, subject = rest.split(" ", 1)
    return {"rev": h, "date": date, "subject": subject, "branch": branch}


def upstream_tail(per_page: int = 8) -> list[dict]:
    url = f"https://api.github.com/repos/{UPSTREAM}/commits?per_page={per_page}"
    req = urllib.request.Request(url, headers={"User-Agent": "nanolika-repo-watch/0.1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    out = []
    for c in data:
        msg = c["commit"]["message"].splitlines()[0]
        date = c["commit"]["committer"]["date"]
        # только первый информативный заголовок; PR-мержи держим одним сообщением
        out.append({"sha": c["sha"][:7], "date": date, "subject": msg})
    return out


def compare() -> dict:
    ours = ours_head()
    up = upstream_tail()
    # есть ли наш HEAD среди их последних (мы ветвёмся от них) — просто информации
    # их последний значимый vs наш: вычленяем subjects, не по.title
    return {"ours": ours, "upstream_head": up[0] if up else None, "upstream_tail": up}


def _fmt_rfc(z: str) -> str:
    try:
        dt = datetime.fromisoformat(z.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%d.%m %H:%M")
    except Exception:
        return z


def render(c: dict) -> str:
    o = c["ours"]
    up = c["upstream_head"]
    lines = [
        f"Слежу за {UPSTREAM}.",
        f"У нас: {o['rev']} ({o['date']}) — {o['subject']}",
    ]
    if up:
        lines.append(f"У них: {up['sha']} ({_fmt_rfc(up['date'])}) — {up['subject']}")
    else:
        lines.append("У них: данных нет (репозиторий недоступен).")
    # заметные новые вещи у них, которых нет у нас (эвристика — по веткам/подписи)
    droppable = ("readme", "contributors", "docs(", "chore(")
    sig = [t for t in c["upstream_tail"] if not t["subject"].lower().startswith(droppable)][:4]
    if sig:
        lines.append("Реальные изменения у них за последние коммиты:")
        for t in sig:
            lines.append(f"  · [{t['date'][:10]}] {t['subject']}")
    lines.append("— превью-наблюдатель; живую ленту не трогает. —")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    c = compare()
    if args.json:
        print(json.dumps(c, ensure_ascii=False, indent=2))
    else:
        print(render(c))


if __name__ == "__main__":
    main()