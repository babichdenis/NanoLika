"""A voice message in a chat: heard through the relay's own doors, answered the same way."""

from __future__ import annotations

import json

import httpx
import pytest

from nanomuse.channels import voice_note


def relay(*, menu: dict | None = None, said: dict | None = None, sound: bytes = b"OGG"):
    """A relay with a menu, a transcription answer and a speech answer; all recorded."""
    calls: list[tuple[str, str, dict]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = request.content
        calls.append((request.method, request.url.path, {}))
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json=menu
                or {
                    "data": [
                        {"id": "chat-1", "kind": "chat", "nanomuse": {}},
                        {
                            "id": "whisper-m",
                            "kind": "audio",
                            "nanomuse": {"audio_kind": "transcribe", "available": True},
                        },
                        {
                            "id": "tts-m",
                            "kind": "audio",
                            "nanomuse": {"audio_kind": "speech", "available": True},
                        },
                    ]
                },
            )
        if request.url.path == "/v1/audio/transcriptions":
            assert b"whisper-m" in body
            return httpx.Response(200, json=said or {"text": "  привет, Лика  "})
        if request.url.path == "/v1/audio/speech":
            return httpx.Response(200, content=sound, headers={"content-type": "audio/ogg"})
        return httpx.Response(404, json={})

    return handler, calls


@pytest.fixture(autouse=True)
def _fresh_menu():
    voice_note.forget_menu()
    yield
    voice_note.forget_menu()


async def test_a_recording_is_heard_with_the_menu_model():
    handler, calls = relay()
    words = await voice_note.hear(
        b"OggS-record",
        "voice.ogg",
        "audio/ogg",
        base="https://relay",
        key="k",
        transport=httpx.MockTransport(handler),
    )
    assert words == "привет, Лика"
    assert [c[1] for c in calls] == ["/v1/models", "/v1/audio/transcriptions"]


async def test_the_answer_is_said_as_opus():
    handler, calls = relay()
    sound, mime = await voice_note.say(
        "готово", base="https://relay", key="k", transport=httpx.MockTransport(handler)
    )
    assert sound == b"OGG" and mime == "audio/ogg"
    assert calls[-1][1] == "/v1/audio/speech"


async def test_without_a_key_nothing_is_asked():
    with pytest.raises(voice_note.VoiceError):
        await voice_note.hear(b"x", "voice.ogg", "audio/ogg", base="https://relay", key="")


async def test_a_menu_without_the_model_says_so_once():
    handler, _ = relay(menu={"data": [{"id": "chat-1", "kind": "chat", "nanomuse": {}}]})
    with pytest.raises(voice_note.VoiceError):
        await voice_note.hear(
            b"x", "voice.ogg", "audio/ogg", base="https://relay", key="k",
            transport=httpx.MockTransport(handler),
        )


# -- the manager: a voice message becomes words, and the answer becomes a voice ---------------


class _Channel:
    supports_voice = True

    def __init__(self):
        self.voices: list[str] = []

    async def send_voice(self, chat_id, path):
        self.voices.append(path)
        assert open(path, "rb").read() == b"OGG"
        return "m1"


@pytest.mark.asyncio
async def test_a_voice_message_is_heard_and_the_answer_is_said_back(tmp_path, monkeypatch):
    from nanomuse.channels.base import InboundFile
    from nanomuse.channels.manager import ChannelManager

    heard: list[tuple] = []
    said: list[tuple] = []

    async def fake_hear(data, filename, mime, *, base, key, model="", language="", transport=None):
        heard.append((base, key, filename, mime))
        return "включи свет"

    async def fake_say(text, *, base, key, model="", voice="", fmt="opus", transport=None):
        said.append((text, base, key))
        return b"OGG", "audio/ogg"

    monkeypatch.setattr("nanomuse.channels.manager.voice_note.hear", fake_hear)
    monkeypatch.setattr("nanomuse.channels.manager.voice_note.say", fake_say)

    manager = ChannelManager.__new__(ChannelManager)
    manager._voice_turns = {}
    channel = _Channel()
    manager.channels = {"telegram": channel}
    manager._redact_outbound = lambda text: text

    item = InboundFile(name="voice.ogg", data=b"OggS", kind="audio")
    words = await manager._hear_one(item, "https://relay", "k")
    assert words == "включи свет"
    assert heard == [("https://relay", "k", "voice.ogg", "audio/ogg")]

    manager._voice_turns["t1"] = ("https://relay", "k")
    await manager._say_back("t1", ("telegram", "1001"), channel, "**Готово**, свет включён.")
    assert said and said[0][0] == "Готово, свет включён."
    assert len(channel.voices) == 1
    assert "t1" not in manager._voice_turns


@pytest.mark.asyncio
async def test_typing_in_a_chat_gets_no_voice_answer():
    from nanomuse.channels.manager import ChannelManager

    manager = ChannelManager.__new__(ChannelManager)
    manager._voice_turns = {}
    manager.channels = {"telegram": _Channel()}
    manager._redact_outbound = lambda text: text

    await manager._say_back("t1", ("telegram", "1001"), manager.channels["telegram"], "привет")
    assert manager.channels["telegram"].voices == []


@pytest.mark.asyncio
async def test_a_refused_recording_leaves_the_turn_as_it_was(monkeypatch):
    from nanomuse.channels.base import InboundFile
    from nanomuse.channels.manager import ChannelManager

    async def refuse(*args, **kwargs):
        raise voice_note.VoiceError("the relay answered 402")

    monkeypatch.setattr("nanomuse.channels.manager.voice_note.hear", refuse)
    manager = ChannelManager.__new__(ChannelManager)
    item = InboundFile(name="voice.ogg", data=b"OggS", kind="audio")
    assert await manager._hear_one(item, "https://relay", "k") == ""
