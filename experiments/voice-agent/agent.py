"""A voice assistant and book reader for Windows: listen, answer, read aloud.

Mic -> faster-whisper -> the relay's chat model -> edge-tts -> speakers, with background
music on its own channel. Books are TXT/EPUB/FB2 under ``books/``; the reader keeps a
bookmark per book.

Run: ``.venv\\Scripts\\python agent.py``. Press Ctrl+C to stop. Commands: "читай <название>",
"продолжай", "пауза", "музыка", "тише", "выключи музыку", "что читаешь".
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path

import httpx
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

from commands import describe_level, parse
from mixer import Mixer
from reader import Reader, find_book

ROOT = Path(__file__).resolve().parent
BOOKS = ROOT / "books"
MUSIC = ROOT / "music"
STATE = ROOT / "state.json"

SAMPLE_RATE = 16000
BLOCK_MS = 30
BLOCK = SAMPLE_RATE * BLOCK_MS // 1000
SILENCE_S = 1.1
MAX_UTTERANCE_S = 20.0
THRESHOLD = 0.012
VOICE = os.environ.get("NANOLIKA_VOICE", "ru-RU-SvetlanaNeural")
RELAY = os.environ.get("NANOLIKA_URL", "https://cloud.nanolika.ru")
MODEL = os.environ.get("NANOLIKA_MODEL", "deepseek-flash")

SYSTEM = (
    "Ты Лика — голосовой помощник. Отвечай кратко и живо, по-русски, одно-три предложения: "
    "ответ читают вслух. Не перечисляй списки без просьбы. Если просят открыть или читать книгу, "
    "скажи коротко, что делаешь это."
)


def key() -> str:
    value = os.environ.get("NANOLIKA_KEY", "").strip()
    if value:
        return value
    path = ROOT / "key.txt"
    if path.exists():
        value = path.read_text("utf-8").strip()
        if value:
            return value
    raise SystemExit("NANOLIKA_KEY is not set: put the key into key.txt (see README)")


def record() -> np.ndarray | None:
    """One utterance: wait for speech, stop after a pause of ``SILENCE_S``."""
    print("… слушаю", flush=True)
    frames: list[np.ndarray] = []
    speech = False
    quiet_blocks = 0
    started = 0.0
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=BLOCK) as stream:
        while True:
            block, _ = stream.read(BLOCK)
            block = block[:, 0].copy()
            rms = float(np.sqrt(np.mean(block * block)))
            if not speech:
                if rms >= THRESHOLD:
                    speech = True
                    started = time.monotonic()
                    frames.append(block)
                continue
            frames.append(block)
            quiet_blocks = quiet_blocks + 1 if rms < THRESHOLD else 0
            if quiet_blocks * BLOCK_MS / 1000 >= SILENCE_S:
                break
            if time.monotonic() - started >= MAX_UTTERANCE_S:
                break
    if not frames:
        return None
    audio = np.concatenate(frames)
    if len(audio) < SAMPLE_RATE // 3:
        return None
    return audio


async def reply(history: list[dict[str, str]], text: str) -> str:
    history.append({"role": "user", "content": text})
    messages = [{"role": "system", "content": SYSTEM}] + history[-12:]
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{RELAY}/v1/chat/completions",
            headers={"Authorization": "Bearer " + key(), "Content-Type": "application/json"},
            json={"model": MODEL, "messages": messages, "stream": False, "max_tokens": 400},
        )
        response.raise_for_status()
        data = response.json()
    answer = ""
    for choice in data.get("choices") or []:
        message = choice.get("message") or {}
        if isinstance(message.get("content"), str):
            answer += message["content"]
    answer = answer.strip() or "Не получилось ответить."
    history.append({"role": "assistant", "content": answer})
    if len(history) > 24:
        del history[: len(history) - 24]
    return answer


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(data: dict) -> None:
    STATE.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")


class Assistant:
    def __init__(self, mixer: Mixer, state: dict) -> None:
        self.mixer = mixer
        self.state = state
        self.reader: Reader | None = None
        self.task: asyncio.Task | None = None

    async def _read(self, reader: Reader) -> None:
        try:
            await reader.read_aloud(self.mixer.say)
        except asyncio.CancelledError:
            pass
        finally:
            self.mixer.stop_voice()
            save_state(self.state)

    async def stop_reading(self) -> None:
        task, self.task = self.task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.mixer.stop_voice()

    async def handle(self, text: str, history: list[dict[str, str]]) -> None:
        action = parse(text)
        if action.kind == "music_on":
            if await self.mixer.music_start():
                await self.mixer.say(
                    f"Музыка, громкость {describe_level(self.mixer.music_level)}."
                )
            else:
                await self.mixer.say("В папке music нет треков.")
        elif action.kind == "music_off":
            await self.mixer.music_stop()
            await self.mixer.say("Музыку выключила.")
        elif action.kind in {"music_up", "music_down"}:
            step = 0.15 if action.kind == "music_up" else -0.15
            level = self.mixer.set_music_level(self.mixer.music_level + step)
            await self.mixer.say(f"Громкость музыки {describe_level(level)}.")
        elif action.kind == "music_volume":
            level = self.mixer.set_music_level(action.value or 0.0)
            await self.mixer.say(f"Громкость музыки {describe_level(level)}.")
        elif action.kind == "read":
            book = find_book(BOOKS, action.text)
            if book is None:
                await self.mixer.say(f"Не нашла книгу «{action.text}» в папке books.")
                return
            await self.stop_reading()
            self.reader = Reader(book, self.state, on_save=lambda: save_state(self.state))
            await self.mixer.say(f"Читаю «{self.reader.title}».")
            self.task = asyncio.create_task(self._read(self.reader))
        elif action.kind == "continue":
            if self.reader is None:
                await self.mixer.say("Скажите «читай» и название книги.")
                return
            if self.task is None or self.task.done():
                self.task = asyncio.create_task(self._read(self.reader))
        elif action.kind == "pause":
            await self.stop_reading()
            await self.mixer.say("Пауза.")
        elif action.kind == "status":
            await self.mixer.say(
                self.reader.where() if self.reader is not None else "Книга не открыта."
            )
        else:
            answer = await reply(history, text)
            await self.mixer.say(answer)


async def main() -> None:
    BOOKS.mkdir(exist_ok=True)
    MUSIC.mkdir(exist_ok=True)
    mixer = Mixer(MUSIC)
    state = load_state()
    assistant = Assistant(mixer, state)
    print("Загружаю слух…", flush=True)
    whisper = WhisperModel("small", device="cpu", compute_type="int8")
    history: list[dict[str, str]] = []
    await mixer.say("Привет. Я Лика. Говорите, я слушаю.")
    try:
        while True:
            audio = await asyncio.to_thread(record)
            if audio is None:
                continue
            segments, _info = whisper.transcribe(audio, language="ru", vad_filter=False, beam_size=1)
            text = " ".join(segment.text.strip() for segment in segments).strip()
            text = re.sub(r"\s+", " ", text)
            if not text:
                continue
            print(f"Вы: {text}", flush=True)
            await assistant.handle(text, history)
    finally:
        await assistant.stop_reading()
        await mixer.close()
        save_state(state)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nПока.")
