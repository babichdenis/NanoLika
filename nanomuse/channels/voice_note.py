"""A voice message in a chat: words heard, an answer said back.

Telegram and MAX deliver a recording as a file, and a text model cannot listen: the
message would reach the agent as an opaque ``voice.ogg``. So the bytes go through the
relay's own doors — ``POST /v1/audio/transcriptions`` hears them, ``POST /v1/audio/speech``
says the answer — with the chat's Cloud key when it has one and this machine's account
otherwise. The price is the app's price: 100 characters and 5 seconds are one unit.

Nothing here may break a turn: a relay without a speech model, a key that lapsed, a
provider that refuses — every one of those means the recording stays a file the person
sent, and the answer stays text.
"""

from __future__ import annotations

import httpx

from nanomuse.logger import logger

#: longer than this is a document, not a phrase: it is said as text and left at that
TEXT_LIMIT = 1200

#: what the relay calls the two kinds of audio model
HEAR = "transcribe"
SAY = "speech"


class VoiceError(RuntimeError):
    """The relay could not hear or say it; the caller keeps the turn as it was."""


#: base + kind -> the model id the menu offers; the menu is asked once per process
_menus: dict[str, str] = {}


def forget_menu() -> None:
    """Drop what was learned about the relay's audio models (a test, or a new key)."""
    _menus.clear()


async def _pick(
    base: str,
    key: str,
    kind: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """The first model of this kind the account may use, from the relay's own menu."""
    slot = f"{base}|{kind}"
    held = _menus.get(slot)
    if held:
        return held
    headers = {"Authorization": f"Bearer {key}"}
    try:
        async with httpx.AsyncClient(transport=transport, timeout=20.0) as client:
            response = await client.get(f"{base}/v1/models", headers=headers)
    except httpx.HTTPError as exc:
        raise VoiceError(f"the relay did not answer: {type(exc).__name__}") from None
    if response.status_code >= 400:
        raise VoiceError(f"the relay answered {response.status_code}")
    try:
        data = (response.json() or {}).get("data") or []
    except ValueError:
        raise VoiceError("the relay sent no model menu") from None
    for entry in data:
        if not isinstance(entry, dict) or entry.get("kind") != "audio":
            continue
        about = entry.get("nanomuse") or {}
        if about.get("audio_kind") != kind or about.get("available") is False:
            continue
        model = str(entry.get("id") or "")
        if model:
            _menus[slot] = model
            return model
    raise VoiceError(f"the menu has no model that can {kind}")


async def hear(
    data: bytes,
    filename: str,
    mime: str,
    *,
    base: str,
    key: str,
    model: str = "",
    language: str = "",
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """The words of a recording; ``""`` when there are none or the relay cannot hear them."""
    if not (base and key):
        raise VoiceError("no relay to ask")
    base = base.rstrip("/")
    chosen = model or await _pick(base, key, HEAR, transport)
    form = {"model": chosen}
    if language:
        form["language"] = language
    headers = {"Authorization": f"Bearer {key}"}
    files = {"file": (filename or "voice.ogg", data, mime or "audio/ogg")}
    try:
        async with httpx.AsyncClient(transport=transport, timeout=300.0) as client:
            response = await client.post(
                f"{base}/v1/audio/transcriptions", data=form, files=files, headers=headers
            )
    except httpx.HTTPError as exc:
        raise VoiceError(f"the relay did not answer: {type(exc).__name__}") from None
    if response.status_code >= 400:
        # a refusal is worth a line: a key that lapsed is the usual reason
        logger.warning("voice: the relay refused the recording (HTTP {})", response.status_code)
        raise VoiceError(f"the relay answered {response.status_code}")
    try:
        said = response.json() or {}
    except ValueError:
        raise VoiceError("the relay sent no words") from None
    return str(said.get("text") or "").strip()


async def say(
    text: str,
    *,
    base: str,
    key: str,
    model: str = "",
    voice: str = "",
    fmt: str = "opus",
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[bytes, str]:
    """The answer as sound; Telegram and MAX both play Opus in a voice bubble."""
    body = text.strip()
    if not body:
        return b"", ""
    if not (base and key):
        raise VoiceError("no relay to ask")
    base = base.rstrip("/")
    chosen = model or await _pick(base, key, SAY, transport)
    payload: dict[str, object] = {"model": chosen, "input": body[:TEXT_LIMIT], "response_format": fmt}
    if voice:
        payload["voice"] = voice
    headers = {"Authorization": f"Bearer {key}"}
    try:
        async with httpx.AsyncClient(transport=transport, timeout=300.0) as client:
            response = await client.post(f"{base}/v1/audio/speech", json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise VoiceError(f"the relay did not answer: {type(exc).__name__}") from None
    if response.status_code >= 400:
        logger.warning("voice: the relay refused to say it (HTTP {})", response.status_code)
        raise VoiceError(f"the relay answered {response.status_code}")
    if not response.content:
        raise VoiceError("the relay sent no sound")
    mime = (response.headers.get("content-type") or "audio/ogg").split(";")[0].strip()
    return response.content, mime


__all__ = ["HEAR", "SAY", "TEXT_LIMIT", "VoiceError", "forget_menu", "hear", "say"]
