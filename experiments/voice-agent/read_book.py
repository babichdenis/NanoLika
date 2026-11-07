"""Read a book aloud without the microphone: the reader on its own.

    .venv\\Scripts\\python read_book.py "название"
    .venv\\Scripts\\python read_book.py --list

Books are TXT/EPUB/FB2 in ``books/``; the place is remembered in ``state.json``.
Ctrl+C stops, the next run continues from the paragraph it reached. For music under the
book, use ``player.py``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mixer import Mixer
from reader import Reader, find_book

ROOT = Path(__file__).resolve().parent
BOOKS = ROOT / "books"
STATE = ROOT / "state.json"


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(data: dict) -> None:
    STATE.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")


async def main() -> None:
    BOOKS.mkdir(exist_ok=True)
    args = sys.argv[1:]
    if not args or args[0] in {"--list", "-l"}:
        books = sorted(p.stem for p in BOOKS.iterdir() if p.suffix.lower() in {".txt", ".epub", ".fb2", ".fbz"})
        print("Книги в books/:")
        for book in books:
            print(" -", book)
        if not books:
            print("  (пусто: положите .txt, .epub или .fb2)")
        return
    wanted = " ".join(args)
    path = find_book(BOOKS, wanted)
    if path is None:
        raise SystemExit(f"Книга «{wanted}» не найдена в {BOOKS}")
    state = load_state()
    reader = Reader(path, state, on_save=lambda: save_state(state))
    mixer = Mixer(BOOKS.parent / "music")
    print(f"Читаю «{reader.title}», с абзаца {reader.index + 1} из {len(reader.paragraphs)}.", flush=True)
    try:
        await reader.read_aloud(mixer.say)
    except KeyboardInterrupt:
        print("\nОстановилась, закладка сохранена.", flush=True)
    finally:
        await mixer.close()
        save_state(state)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
