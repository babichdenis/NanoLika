#!/usr/bin/env python3
"""Who writes to the bot: channel, sender, when — never the words.

Reads the bot's touches log (one JSON line per inbound message) and prints one block per
sender: how many messages, when the first and the last came, how long they were, and
whether the sender is on the operator's own list.

    python3 scripts/who_bots.py            # everything in the log
    python3 scripts/who_bots.py 24         # only the last 24 hours
    python3 scripts/who_bots.py 24 max     # only one channel
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_LOG = "/opt/nanolika-bot/workspace/touches.jsonl"


def _rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).astimezone().strftime("%d.%m %H:%M")


def main(argv: list[str]) -> int:
    hours = float(argv[1]) if len(argv) > 1 else 0.0
    channel = argv[2].lower() if len(argv) > 2 else ""
    path = Path(os.environ.get("TOUCH_LOG") or DEFAULT_LOG)
    rows = _rows(path)
    if not rows:
        print(f"обращений пока нет: {path} пуст или не найден")
        return 0
    since = time.time() - hours * 3600 if hours > 0 else 0
    picked = [r for r in rows if float(r.get("ts") or 0) >= since and (not channel or str(r.get("channel")) == channel)]
    if not picked:
        print(f"за {hours:g} ч обращений нет")
        return 0
    per: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in picked:
        per[(str(row.get("channel") or "?"), str(row.get("sender") or "?"))].append(row)
    label = f"за {hours:g} ч" if hours else "за всё время"
    if channel:
        label += f", канал {channel}"
    print(f"{label}: {len(picked)} обращений, {len(per)} отправителей\n")
    for (chan, sender), items in sorted(per.items(), key=lambda kv: -len(kv[1])):
        first = min(float(i.get("ts") or 0) for i in items)
        last = max(float(i.get("ts") or 0) for i in items)
        avg = sum(int(i.get("chars") or 0) for i in items) / max(len(items), 1)
        print(f"{chan:9} {sender:>14}  {len(items):4} сообщ.  {_when(first)} — {_when(last)}  ~{avg:.0f} симв.")
    print("\nПодсказка: свой id — тот, что отвечает вам в личке бота; остальные пришли со стороны.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
