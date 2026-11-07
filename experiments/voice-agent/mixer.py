"""Two audio channels: background music and the voice, with ducking between them.

pygame's ``music`` module is a single stream, so music and voice each get their own
``Channel``: the voice channel takes edge-tts clips, the music channel loops a playlist
from ``music/``. While the voice speaks, the music drops to a quarter of its volume.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from pathlib import Path
from typing import Iterable

import edge_tts
import pygame

AUDIO = {".mp3", ".ogg", ".wav", ".flac"}
MUSIC_CHANNEL = 0
VOICE_CHANNEL = 1
RENDER_TIMEOUT = 20.0
RENDER_TRIES = 3


class Mixer:
    """Music on channel 0, voice on channel 1, one volume knob for the music."""

    def __init__(
        self,
        music_dir: Path,
        voice: str | None = None,
        rate: str | None = None,
        pitch: str | None = None,
    ) -> None:
        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
        pygame.mixer.set_num_channels(8)
        self.music_dir = music_dir
        self.voice = voice or os.environ.get("NANOLIKA_VOICE", "ru-RU-SvetlanaNeural")
        self.rate = rate or os.environ.get("NANOLIKA_RATE", "-10%")
        self.pitch = pitch or os.environ.get("NANOLIKA_PITCH", "-6Hz")
        self.tracks = (
            sorted(p for p in music_dir.iterdir() if p.suffix.lower() in AUDIO)
            if music_dir.is_dir()
            else []
        )
        self.music_level = 0.35
        self.duck_level = 0.25
        self.cache_dir = music_dir.parent / "cache"
        self.cache_dir.mkdir(exist_ok=True)
        self._music = pygame.mixer.Channel(MUSIC_CHANNEL)
        self._voice = pygame.mixer.Channel(VOICE_CHANNEL)
        self._music_on = False
        self._music_task: asyncio.Task | None = None
        self._track_index = 0
        self._current: pygame.mixer.Sound | None = None
        self._playing_name: str | None = None

    # music -----------------------------------------------------------------------------

    def music_playing(self) -> bool:
        return self._music_on

    def current_track(self) -> str | None:
        return self._playing_name

    def set_music_level(self, value: float) -> float:
        self.music_level = max(0.0, min(1.0, value))
        self._apply()
        return self.music_level

    def _apply(self) -> None:
        level = self.music_level
        if self._voice.get_busy():
            level *= self.duck_level
        self._music.set_volume(level)

    async def music_start(self) -> bool:
        if self._music_on:
            return True
        if not self.tracks:
            return False
        self._music_on = True
        self._music_task = asyncio.create_task(self._music_loop())
        await asyncio.sleep(0)
        return True

    async def music_stop(self) -> None:
        self._music_on = False
        self._playing_name = None
        self._music.fadeout(600)
        task, self._music_task = self._music_task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 - a broken track must not stop the mixer
                pass

    def skip_track(self) -> None:
        """Move to the next track: the music loop sees a free channel and picks the next one."""
        if self._music_on:
            self._music.stop()

    async def _music_loop(self) -> None:
        while self._music_on:
            if not self.tracks:
                await asyncio.sleep(1.0)
                continue
            track = self.tracks[self._track_index % len(self.tracks)]
            self._track_index += 1
            try:
                self._current = pygame.mixer.Sound(str(track))
            except pygame.error as error:
                print(f"Не смогла открыть трек {track.name}: {error}", flush=True)
                continue
            self._playing_name = track.stem
            self._apply()
            self._music.play(self._current, fade_ms=800)
            deadline = time.monotonic() + self._current.get_length() + 10.0
            while self._music_on and self._music.get_busy() and time.monotonic() < deadline:
                await asyncio.sleep(0.2)
            if self._music_on:
                await asyncio.sleep(0.6)

    # voice -----------------------------------------------------------------------------

    def voice_busy(self) -> bool:
        return bool(self._voice.get_busy())

    def stop_voice(self) -> None:
        self._voice.stop()
        self._apply()

    async def say(self, text: str) -> None:
        await self.say_many([text])

    async def say_many(self, texts: Iterable[str]) -> None:
        """Speak the pieces in order, rendering the next one while the current one plays."""
        pieces = [text.strip() for text in texts if text and text.strip()]
        if not pieces:
            return
        next_task: asyncio.Task | None = None
        index = 0
        while index < len(pieces):
            if next_task is None:
                next_task = asyncio.create_task(self._render(pieces[index]))
            try:
                path = await next_task
            except Exception as error:  # noqa: BLE001 - a silent voice beats a frozen one
                print(f"Голос не ответил: {error}", flush=True)
                next_task = None
                index += 1
                continue
            next_task = (
                asyncio.create_task(self._render(pieces[index + 1]))
                if index + 1 < len(pieces)
                else None
            )
            print(f"Лика: {pieces[index]}", flush=True)
            try:
                await self._play(path)
            except pygame.error as error:
                print(f"Не смогла проиграть фразу: {error}", flush=True)
            index += 1

    async def _render(self, text: str) -> Path:
        """Render one phrase to mp3, with a cache: the same text is never synthesised twice."""
        digest = hashlib.sha1(
            f"{self.voice}\n{self.rate}\n{self.pitch}\n{text}".encode("utf-8")
        ).hexdigest()[:20]
        cached = self.cache_dir / f"{digest}.mp3"
        if cached.exists() and cached.stat().st_size > 0:
            return cached
        partial = self.cache_dir / f"{digest}.{time.time_ns()}.part"
        last_error: Exception | None = None
        for attempt in range(1, RENDER_TRIES + 1):
            try:
                await asyncio.wait_for(
                    edge_tts.Communicate(
                        text, self.voice, rate=self.rate, pitch=self.pitch
                    ).save(str(partial)),
                    RENDER_TIMEOUT,
                )
                if partial.exists() and partial.stat().st_size > 0:
                    partial.replace(cached)
                    return cached
                last_error = RuntimeError("пустой ответ озвучки")
            except Exception as error:  # noqa: BLE001 - retry, then report to the caller
                last_error = error
                partial.unlink(missing_ok=True)
                if attempt < RENDER_TRIES:
                    await asyncio.sleep(0.8 * attempt)
        raise RuntimeError(f"edge-tts не ответил после {RENDER_TRIES} попыток: {last_error}")

    async def _play(self, path: Path) -> None:
        sound = pygame.mixer.Sound(str(path))
        self._voice.stop()
        self._voice.play(sound)
        self._apply()
        # A sound that never reports itself finished must not freeze the reader: past its
        # own length plus a margin, move on.
        deadline = time.monotonic() + sound.get_length() + 5.0
        try:
            while self._voice.get_busy() and time.monotonic() < deadline:
                await asyncio.sleep(0.04)
        finally:
            self._voice.stop()
            self._apply()

    async def close(self) -> None:
        await self.music_stop()
        self.stop_voice()
        pygame.mixer.quit()
