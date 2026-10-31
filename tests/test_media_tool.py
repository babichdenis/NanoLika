"""The media tool: the model's own way to speak and to hear a recording."""

from __future__ import annotations

import asyncio
from pathlib import Path

from nanomuse.cloud import CloudError
from nanomuse.tools.media import Media


class FakeCloud:
    def __init__(self) -> None:
        self.spoken: list[dict] = []
        self.heard: list[dict] = []

    async def models(self):
        return [
            {"id": "text", "nanomuse": {"kind": "chat"}},
            {"id": "voice", "nanomuse": {"kind": "audio", "audio_kind": "speech"}},
            {"id": "ears", "nanomuse": {"kind": "audio", "audio_kind": "transcribe"}},
        ]

    async def speech(self, model, text, *, voice="", fmt="mp3", speed=None):
        self.spoken.append({"model": model, "text": text, "fmt": fmt, "speed": speed})
        return b"ID3fake", "audio/mpeg", {"x-nanomuse-charged": "100", "x-nanomuse-seconds": "3.20"}

    async def transcribe(self, model, filename, data, mime, *, language=""):
        self.heard.append({"model": model, "name": filename, "mime": mime, "bytes": len(data)})
        return {"text": "привет", "nanomuse": {"charged": 83, "seconds": 3.0}}


def tool(tmp_path: Path, cloud=None) -> Media:
    return Media(workspace=tmp_path, extra_roots=[], cloud=lambda: cloud or FakeCloud())


def test_speech_saves_a_file_and_reports_the_charge(tmp_path: Path):
    cloud = FakeCloud()
    result = asyncio.run(tool(tmp_path, cloud).execute(action="speech", text="привет", format="mp3"))
    assert result.ok, result.error
    assert "media/" in result.output and "3.20 s" in result.output and "charged 100" in result.output
    files = list((tmp_path / "media").glob("*.mp3"))
    assert len(files) == 1 and files[0].read_bytes() == b"ID3fake"
    assert cloud.spoken[-1]["text"] == "привет"


def test_speech_needs_the_words(tmp_path: Path):
    result = asyncio.run(tool(tmp_path).execute(action="speech", text="  "))
    assert not result.ok and "text" in result.error


def test_transcribe_reads_only_inside_the_workspace(tmp_path: Path):
    recording = tmp_path / "voice.ogg"
    recording.write_bytes(b"OggS" + b"\x00" * 20)
    result = asyncio.run(tool(tmp_path).execute(action="transcribe", path="voice.ogg"))
    assert result.ok and "привет" in result.output and "3.0 s" in result.output
    outside = asyncio.run(tool(tmp_path).execute(action="transcribe", path="/etc/passwd"))
    assert not outside.ok


def test_a_relay_without_the_model_says_so(tmp_path: Path):
    class NoAudio(FakeCloud):
        async def models(self):
            return [{"id": "text", "nanomuse": {"kind": "chat"}}]

    result = asyncio.run(tool(tmp_path, NoAudio()).execute(action="speech", text="hi"))
    assert not result.ok and "no model that can speech" in result.error


def test_a_relay_error_is_one_sentence(tmp_path: Path):
    class Refusing(FakeCloud):
        async def speech(self, *a, **kw):
            raise CloudError(402, "insufficient_funds", "Not enough money for this voice")

    result = asyncio.run(tool(tmp_path, Refusing()).execute(action="speech", text="hi"))
    assert not result.ok and "Not enough money" in result.error


def test_signed_out_refuses(tmp_path: Path):
    result = asyncio.run(Media(workspace=tmp_path, extra_roots=[], cloud=lambda: None).execute(action="speech", text="hi"))
    assert not result.ok and "Sign in" in result.error
