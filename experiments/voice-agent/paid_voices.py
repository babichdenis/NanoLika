"""Paid voices through the relay: the same paragraph, several engines and voices.

The relay already holds provider keys, so the account key is enough:

    relay  /v1/audio/speech   model=voice      Gemini TTS (voices Kore, Puck, Leda)
                              model=voice-hd   MiniMax Speech HD
                              model=bh-tts-1-hd
    keys/elevenlabs.txt, keys/openai.txt, keys/azure.txt   optional direct providers

Run: .venv\\Scripts\\python -u paid_voices.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import edge_tts
import httpx
import pygame

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "voices-demo"
KEYS = ROOT / "keys"
ANNOUNCE_VOICE = "ru-RU-SvetlanaNeural"
RELAY = "https://cloud.nanolika.ru"

TEXT = (
    "В конце ноября, в оттепель, часов в девять утра, поезд "
    "Петербургско-Варшавской железной дороги на всех парах подходил к Петербургу. "
    "Было так сыро и туманно, что насилу рассвело."
)

SAMPLES = [
    ("Gemini, голос Kore", "voice", "Kore"),
    ("Gemini, голос Puck", "voice", "Puck"),
    ("Gemini, голос Leda", "voice", "Leda"),
    ("MiniMax Speech HD", "voice-hd", None),
    ("TTS 1 HD", "bh-tts-1-hd", None),
]


def read_key(name: str) -> list[str]:
    path = KEYS / f"{name}.txt"
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text("utf-8").splitlines() if line.strip()]


def relay_key() -> str:
    path = ROOT / "key.txt"
    return path.read_text("utf-8").strip() if path.exists() else ""


async def play(path: Path, volume: float = 0.9) -> None:
    pygame.mixer.music.load(str(path))
    pygame.mixer.music.set_volume(volume)
    pygame.mixer.music.play()
    while pygame.mixer.music.get_busy():
        await asyncio.sleep(0.05)
    pygame.mixer.music.unload()


async def announce(text: str) -> None:
    path = OUT / "announce-paid.mp3"
    await edge_tts.Communicate(text, ANNOUNCE_VOICE, rate="-5%").save(str(path))
    await play(path)
    await asyncio.sleep(0.3)


async def relay_speech(
    client: httpx.AsyncClient, key: str, model: str, voice: str | None, path: Path
) -> None:
    payload: dict[str, str] = {"model": model, "input": TEXT, "response_format": "mp3"}
    if voice:
        payload["voice"] = voice
    response = await client.post(
        f"{RELAY}/v1/audio/speech",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
    )
    response.raise_for_status()
    path.write_bytes(response.content)


async def elevenlabs(key: str, out: Path) -> None:
    async with httpx.AsyncClient(timeout=90) as client:
        for name, voice_id in (("Алиса", "Xb7hH8MSUJpSbSDYk0k2"), ("Рейчел", "21m00Tcm4TlvDq8ikWAM")):
            response = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                headers={"xi-api-key": key, "Content-Type": "application/json"},
                json={"text": TEXT, "model_id": "eleven_multilingual_v2"},
            )
            response.raise_for_status()
            (out / f"elevenlabs-{name}.mp3").write_bytes(response.content)


async def openai(key: str, out: Path) -> None:
    async with httpx.AsyncClient(timeout=90) as client:
        for voice in ("nova", "alloy"):
            response = await client.post(
                "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": "gpt-4o-mini-tts",
                    "voice": voice,
                    "input": TEXT,
                    "response_format": "mp3",
                },
            )
            response.raise_for_status()
            (out / f"openai-{voice}.mp3").write_bytes(response.content)


async def main() -> None:
    OUT.mkdir(exist_ok=True)
    KEYS.mkdir(exist_ok=True)
    pygame.mixer.init()
    made: list[tuple[str, Path]] = []

    token = relay_key()
    if token:
        async with httpx.AsyncClient(timeout=120) as client:
            for label, model, voice in SAMPLES:
                safe = label.replace(", ", "-").replace(" ", "-").lower()
                path = OUT / f"relay-{safe}.mp3"
                try:
                    await relay_speech(client, token, model, voice, path)
                    made.append((label, path))
                    print(f"готово: {label}", flush=True)
                except Exception as error:  # noqa: BLE001 - one dead engine must not stop the rest
                    print(f"{label} не ответил: {error}", flush=True)

    for name, loader in (("elevenlabs", elevenlabs), ("openai", openai)):
        lines = read_key(name)
        if not lines:
            continue
        try:
            await loader(lines[0], OUT)
        except Exception as error:  # noqa: BLE001
            print(f"{name} не ответил: {error}", flush=True)

    for path in sorted(OUT.glob("elevenlabs-*.mp3")):
        made.append(("ElevenLabs, " + path.stem.split("-", 1)[1], path))
    for path in sorted(OUT.glob("openai-*.mp3")):
        made.append(("OpenAI, голос " + path.stem.split("-", 1)[1], path))

    if not made:
        print("Нечего играть: нет ключа релея и нет ключей провайдеров.", flush=True)
        await announce("Ключей нет. Положите ключ в папку keys.")
        pygame.mixer.quit()
        return

    for index, (label, path) in enumerate(made, 1):
        print(f"{index}. {label}", flush=True)
        await announce(f"Вариант {index}. {label}.")
        await play(path)
        await asyncio.sleep(0.5)
    pygame.mixer.quit()
    print("готово", flush=True)


asyncio.run(main())
