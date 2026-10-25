#!/usr/bin/env python3
"""Написать ``CAPABILITIES.md`` — что умеет сборка, для людей и для файловых агентов.

Инвентарь берётся из живого реестра инструментов (`nanomuse.capabilities`), а не пишется
руками: список расходится с кодом ровно настолько, насколько его забыли обновить, и это
случалось. Поэтому файл генерируется, а ``--check`` не даёт закоммитить устаревший.

    python3 scripts/capabilities_md.py            # перезаписать CAPABILITIES.md
    python3 scripts/capabilities_md.py --check    # только сказать, устарел ли

Тем же файлом пользуются агенты, которые читают репозиторий: он лежит в корне рядом с
AGENTS.md и CLAUDE.md, а те на него ссылаются (docs/store.md — про склад, этот — про
встроенное).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "CAPABILITIES.md"


def build() -> str:
    """Собрать текст: приложение поднимается в песочнице без сети и без модели."""
    import tempfile

    from nanomuse import capabilities
    from nanomuse.app import NanoMuseApp
    from nanomuse.config import Settings
    from nanomuse.ui import HeadlessUI

    with tempfile.TemporaryDirectory(prefix="capabilities-") as tmp:
        folder = Path(tmp)
        settings = Settings()
        settings.data_dir = folder
        settings.agent.workspace = folder / "workspace"
        # всё, что можно включить без секретов, включаем: файл описывает возможности, а не
        # однажды выбранную конфигурацию
        settings.connectors.email.enabled = True
        settings.connectors.email.imap_host = "imap.example.com"
        settings.connectors.email.address = "someone@example.com"
        settings.connectors.email.password = "not-a-secret"
        settings.connectors.calendar.enabled = True
        settings.connectors.contacts.enabled = True
        settings.hands.enabled = True
        app = NanoMuseApp(settings, ui=HeadlessUI(), session_id="capabilities")
        data = capabilities.inventory(app.tools, settings)
        text = capabilities.render_markdown(data)
    header = (
        "<!-- Сгенерировано scripts/capabilities_md.py — не править руками. -->\n"
        "<!-- Читать вместе с AGENTS.md (правила) и docs/store.md (склад). -->\n\n"
    )
    return header + text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="инвентарь возможностей сборки")
    parser.add_argument("--check", action="store_true", help="не писать, только сравнить")
    args = parser.parse_args(argv)

    fresh = build()
    if args.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != fresh:
            print(f"{TARGET.name} устарел: запустить scripts/capabilities_md.py", file=sys.stderr)
            return 1
        print(f"{TARGET.name} в порядке")
        return 0
    TARGET.write_text(fresh, encoding="utf-8")
    lines = fresh.count("\n")
    print(f"записан {TARGET} ({lines} строк)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
