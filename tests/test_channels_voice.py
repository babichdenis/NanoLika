"""A voice message in a chat: heard through the relay's own doors, answered the same way."""

from __future__ import annotations

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
    # the shelf is asked first; this relay has none, so the plain menu answers
    assert [c[1] for c in calls] == ["/v1/me/models", "/v1/models", "/v1/audio/transcriptions"]


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
            b"x",
            "voice.ogg",
            "audio/ogg",
            base="https://relay",
            key="k",
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

    manager._remember_voice_turn("t1", "https://relay", "k")
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


# -- the cache: a menu with a clock, and a retry when the model it named is gone ------------


async def test_a_menu_answer_older_than_the_ttl_is_asked_again(monkeypatch):
    handler, calls = relay()
    monkeypatch.setattr(voice_note, "MENU_TTL_S", 0.0)
    for _ in range(2):
        await voice_note.hear(
            b"OggS",
            "voice.ogg",
            "audio/ogg",
            base="https://relay",
            key="k",
            transport=httpx.MockTransport(handler),
        )
    # `/v1/me/models` is probed once (this relay has no shelf), then the plain menu is read
    menus = [c[1] for c in calls if c[1] == "/v1/models"]
    assert len(menus) == 2, "a menu with no clock breaks the voice silently until a restart"


async def test_a_model_gone_from_the_menu_is_asked_for_once_more(monkeypatch):
    """The relay drops the model we cached (a plan, a rotation): the next phrase re-asks
    instead of failing until the process restarts."""
    rounds = {"menu": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/me/models":
            rounds["menu"] += 1
            model = "old-tts" if rounds["menu"] == 1 else "new-tts"
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "id": model,
                            "kind": "audio",
                            "nanomuse": {"audio_kind": "speech", "available": True},
                        }
                    ],
                    "shelf": {"enabled": {"audio": [model]}, "default": {"audio": model}},
                },
            )
        if request.url.path == "/v1/audio/speech":
            if b"old-tts" in request.content:
                return httpx.Response(404, json={"error": {"code": "model_not_offered"}})
            return httpx.Response(200, content=b"OGG", headers={"content-type": "audio/ogg"})
        return httpx.Response(404, json={})

    sound, _ = await voice_note.say(
        "готово", base="https://relay", key="k", transport=httpx.MockTransport(handler)
    )
    assert sound == b"OGG" and rounds["menu"] == 2


# -- the manager: the account key of a spoken chat does not stay in memory ------------------


@pytest.mark.asyncio
async def test_a_voice_turn_key_is_dropped_when_it_goes_stale(monkeypatch):
    from nanomuse.channels import manager as mgr

    manager = mgr.ChannelManager.__new__(mgr.ChannelManager)
    manager._voice_turns = {}
    manager._remember_voice_turn("t1", "https://relay", "k1")
    assert "t1" in manager._voice_turns
    # a turn that never reached its answer leaves the entry; the next spoken turn clears it
    monkeypatch.setattr(mgr, "VOICE_TURN_TTL_S", 0.0)
    manager._remember_voice_turn("t2", "https://relay", "k2")
    assert "t1" not in manager._voice_turns and "t2" in manager._voice_turns


@pytest.mark.asyncio
async def test_the_voice_turn_map_is_bounded(monkeypatch):
    from nanomuse.channels import manager as mgr

    manager = mgr.ChannelManager.__new__(mgr.ChannelManager)
    manager._voice_turns = {}
    monkeypatch.setattr(mgr, "VOICE_TURNS_MAX", 4)
    for i in range(10):
        manager._remember_voice_turn(f"t{i}", "https://relay", f"k{i}")
    assert len(manager._voice_turns) == 4


async def test_the_cabinet_catalogue_names_the_voice_kind_its_own_way():
    """`/v1/me/models` rows say `audio: {kind}` and carry the shelf; the phrase must be
    heard with the model the person keeps, not the first one on the menu."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/me/models":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "id": "whisper-large",
                            "kind": "audio",
                            "audio": {"kind": "transcribe"},
                            "available": True,
                        },
                        {
                            "id": "whisper-tiny",
                            "kind": "audio",
                            "audio": {"kind": "transcribe"},
                            "available": True,
                        },
                    ],
                    "shelf": {
                        "enabled": {"audio": ["whisper-tiny"]},
                        "default": {"audio": "whisper-tiny"},
                    },
                },
            )
        if request.url.path == "/v1/audio/transcriptions":
            assert b"whisper-tiny" in request.content
            return httpx.Response(200, json={"text": "слово"})
        return httpx.Response(404, json={})

    words = await voice_note.hear(
        b"OggS",
        "voice.ogg",
        "audio/ogg",
        base="https://relay",
        key="k",
        transport=httpx.MockTransport(handler),
    )
    assert words == "слово"
