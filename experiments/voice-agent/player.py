"""Music on one channel, a book on the other: hotkeys in the window, or typed commands.

    .venv\\Scripts\\python -u player.py --keys --music --read идиот   (hotkeys)
    .venv\\Scripts\\python -u player.py                              (typed commands)

Hotkeys: space pauses and resumes, +/- set the music volume, M turns music on or off,
N skips the track, S says where we are, Q quits. The window title shows the book, the
paragraph and the music volume.

No microphone needed. While the book speaks, the music ducks to a quarter of its volume
on its own.
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import logging
import os
import sys
import time
from pathlib import Path

from commands import Action, describe_level, parse
from mixer import Mixer
from reader import Reader, find_book

ROOT = Path(__file__).resolve().parent
BOOKS = ROOT / "books"
MUSIC = ROOT / "music"
STATE = ROOT / "state.json"
LIBRARY = ROOT / "library.json"
LOG = ROOT / "lika.log"
STEP = 0.15

HELP = """\
музыка            включить музыку из music/
тише / громче     громкость музыки шагом 15%
музыка на 20      музыка на 20%
выключи музыку    музыка замолкает
читай идиот       читать книгу из books/ с закладки
пауза             остановить чтение
продолжай         читать дальше
что читаешь       место в книге и громкость музыки
выход             закрыть"""

KEYS = """\
пробел   пауза и продолжай
+ / -    громкость музыки
M        музыка включить или выключить
N        следующий трек
S        где мы
Q        выход"""


def load_library() -> dict[str, str]:
    try:
        data = json.loads(LIBRARY.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(key): str(value) for key, value in data.items()} if isinstance(data, dict) else {}


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def setup_log() -> None:
    logging.basicConfig(
        filename=str(LOG),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8",
    )


_MUTEX = None


def single_instance() -> bool:
    """One window drives the speakers: a second copy would fight for the audio device."""
    global _MUTEX
    if os.name != "nt":
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        _MUTEX = kernel32.CreateMutexW(None, False, "Local\\nanolika-player")
        return bool(_MUTEX) and kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    except Exception:  # noqa: BLE001 - a missing mutex must not block playback
        return True


def save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), "utf-8")


class Player:
    def __init__(self, mixer: Mixer, state: dict, library: dict[str, str] | None = None) -> None:
        self.mixer = mixer
        self.state = state
        self.library = library or {}
        self.reader: Reader | None = None
        self.task: asyncio.Task | None = None

    async def _read(self, reader: Reader) -> None:
        try:
            await reader.read_aloud(self.mixer.say)
            paused = False
            logging.info("книга закончилась: %s", reader.title)
        except asyncio.CancelledError:
            paused = True
            logging.info("пауза: %s, абзац %s", reader.title, reader.index + 1)
        except Exception as error:  # noqa: BLE001 - report and keep the console alive
            logging.exception("чтение сорвалось")
            print(f"Чтение сорвалось: {error}", flush=True)
            paused = True
        finally:
            self.mixer.stop_voice()
            save_state(self.state)
            if paused:
                print(f"Пауза: «{reader.title}», абзац {reader.index + 1}.", flush=True)

    async def stop_reading(self) -> None:
        task, self.task = self.task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.mixer.stop_voice()

    async def start_reading(self) -> None:
        if self.reader is None:
            print("Сначала скажите «читай» и название книги.", flush=True)
            return
        if self.task is not None and not self.task.done():
            return
        self.task = asyncio.create_task(self._read(self.reader))

    def reading(self) -> bool:
        return self.task is not None and not self.task.done()

    async def toggle_pause(self) -> None:
        if self.reading():
            await self.stop_reading()
            print("Пауза.", flush=True)
        elif self.reader is not None:
            await self.start_reading()

    def update_title(self) -> None:
        """The window title is the status bar: book, paragraph, music volume, play state."""
        if os.name != "nt":
            return
        if self.reader is not None:
            place = (
                f"{self.reader.title}, абзац {self.reader.index + 1} "
                f"из {len(self.reader.paragraphs)}"
            )
        else:
            place = "книга не открыта"
        music = (
            f"музыка {describe_level(self.mixer.music_level)}"
            if self.mixer.music_playing()
            else "музыка выкл"
        )
        mark = ">" if self.reading() else "||"
        try:
            ctypes.windll.kernel32.SetConsoleTitleW(f"Лика {mark} {place}, {music}")
        except Exception:  # noqa: BLE001 - a title is decoration, never a reason to stop
            pass

    async def handle(self, action: Action) -> None:
        logging.info("команда: %s %s", action.kind, action.text)
        if action.kind == "music_on":
            if await self.mixer.music_start():
                print(
                    f"Музыка: {self.mixer.current_track() or '…'}, "
                    f"громкость {describe_level(self.mixer.music_level)}.",
                    flush=True,
                )
            else:
                print(f"Папка {MUSIC} пуста: положите туда mp3, ogg или wav.", flush=True)
        elif action.kind == "music_off":
            await self.mixer.music_stop()
            print("Музыка выключена.", flush=True)
        elif action.kind in {"music_up", "music_down"}:
            sign = STEP if action.kind == "music_up" else -STEP
            level = self.mixer.set_music_level(self.mixer.music_level + sign)
            print(f"Музыка: {describe_level(level)}.", flush=True)
        elif action.kind == "music_volume":
            level = self.mixer.set_music_level(action.value or 0.0)
            print(f"Музыка: {describe_level(level)}.", flush=True)
        elif action.kind == "read":
            book = find_book(BOOKS, action.text)
            if book is None:
                names = ", ".join(sorted(p.stem for p in BOOKS.iterdir())) or "пусто"
                print(f"Не нашла «{action.text}». В books/: {names}.", flush=True)
                return
            await self.stop_reading()
            self.reader = Reader(
                book,
                self.state,
                title=self.library.get(book.stem),
                on_save=lambda: save_state(self.state),
            )
            logging.info("книга: %s, с абзаца %s", self.reader.title, self.reader.index + 1)
            print(
                f"Читаю «{self.reader.title}» с абзаца {self.reader.index + 1}.",
                flush=True,
            )
            await self.start_reading()
        elif action.kind == "continue":
            await self.start_reading()
        elif action.kind == "pause":
            await self.stop_reading()
        elif action.kind == "status":
            if self.reader is not None:
                print(self.reader.where(), flush=True)
            music = (
                f"музыка {describe_level(self.mixer.music_level)}"
                if self.mixer.music_playing()
                else "музыка выключена"
            )
            print(f"Звук: {music}.", flush=True)
        else:
            print("Не поняла. «помощь» — что я умею.", flush=True)


def read_key() -> str | None:
    """One keypress from the console, without Enter, or None when nothing is pressed."""
    if os.name != "nt":
        return None
    try:
        import msvcrt
    except ImportError:
        return None
    try:
        if not msvcrt.kbhit():
            return None
        char = msvcrt.getwch()
    except OSError:
        return None
    if char in {"\x00", "\xe0"}:
        try:
            msvcrt.getwch()
        except OSError:
            pass
        return None
    return char


async def run_keys(player: Player, seconds: float = 0.0) -> None:
    print(KEYS, flush=True)
    started = time.monotonic()
    last = 0.0
    while True:
        if seconds and time.monotonic() - started > seconds:
            return
        key = read_key()
        if key is not None:
            low = key.lower()
            if low in {"q", "\x1b"}:
                return
            if key == " ":
                await player.toggle_pause()
            elif key in {"+", "="}:
                await player.handle(Action("music_up"))
            elif key == "-":
                await player.handle(Action("music_down"))
            elif low == "m":
                await player.handle(
                    Action("music_off" if player.mixer.music_playing() else "music_on")
                )
            elif low == "n":
                player.mixer.skip_track()
            elif low == "s":
                await player.handle(Action("status"))
            player.update_title()
            last = time.monotonic()
        elif time.monotonic() - last > 0.5:
            player.update_title()
            last = time.monotonic()
        await asyncio.sleep(0.05)


async def run_commands(player: Player) -> None:
    print("Лика: команды вводятся здесь. «помощь» — список, «выход» — закрыть.", flush=True)
    while True:
        try:
            line = await asyncio.to_thread(input, "> ")
        except EOFError:
            return
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if low in {"выход", "quit", "exit", "q"}:
            return
        if low in {"помощь", "help", "?"}:
            print(HELP, flush=True)
            continue
        await player.handle(parse(line))


async def main() -> None:
    BOOKS.mkdir(exist_ok=True)
    MUSIC.mkdir(exist_ok=True)
    args = sys.argv[1:]
    logging.info("запуск: %s", " ".join(args) or "без аргументов")
    state = load_state()
    mixer = Mixer(MUSIC)
    player = Player(mixer, state, load_library())
    if not mixer.tracks:
        print(f"В {MUSIC} пока нет музыки: положите mp3, ogg или wav.", flush=True)
    if "--music" in args:
        await player.handle(Action("music_on"))
    if "--read" in args:
        wanted = " ".join(args[args.index("--read") + 1 :]).strip()
        if wanted:
            await player.handle(Action("read", text=wanted))
    try:
        if "--daemon" in args:
            minutes = 0.0
            if "--minutes" in args:
                index = args.index("--minutes")
                if index + 1 < len(args):
                    minutes = float(args[index + 1])
            deadline = time.monotonic() + minutes * 60 if minutes else None
            while True:
                await asyncio.sleep(1)
                if deadline is not None and time.monotonic() >= deadline:
                    print("Таймер вышел, останавливаюсь.", flush=True)
                    logging.info("таймер: остановка после %s минут", minutes)
                    break
        elif "--keys" in args:
            limit = 0.0
            if "--seconds" in args:
                index = args.index("--seconds")
                if index + 1 < len(args):
                    limit = float(args[index + 1])
            await run_keys(player, limit)
        else:
            await run_commands(player)
    finally:
        await player.stop_reading()
        await mixer.close()
        save_state(state)
    print("Пока.", flush=True)
    logging.info("выход")


if __name__ == "__main__":
    setup_log()
    if not single_instance():
        print("Лика уже запущена в другом окне: закройте его или работайте там.", flush=True)
        logging.warning("второй запуск отклонён")
        raise SystemExit(0)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("остановлено с клавиатуры")
    except Exception:
        logging.exception("падение")
        raise
