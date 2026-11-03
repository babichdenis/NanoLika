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

import time

import httpx

from nanomuse.logger import logger

#: longer than this is a document, not a phrase: it is said as text and left at that
TEXT_LIMIT = 1200

#: what the relay calls the two kinds of audio model
HEAR = "transcribe"
SAY = "speech"

#: A phrase is heard in seconds; a relay that hangs must not hold the chat's inbound queue
#: for the five minutes the first version waited. The turn stays text on a timeout.
HEAR_TIMEOUT_S = 30.0
SAY_TIMEOUT_S = 30.0

#: How long a menu answer is trusted. The model behind it can leave the menu (a plan, a
#: provider rotation, a limit), and a cache without a clock then breaks the voice silently
#: until the process restarts. A 404 also drops the entry, so the next phrase re-asks.
MENU_TTL_S = 600.0


class VoiceError(RuntimeError):
    """The relay could not hear or say it; the caller keeps the turn as it was."""


#: base + kind -> (the model id, when the menu was read); bounded by the TTL below
_menus: dict[str, tuple[str, float]] = {}

#: relays that answered 404 to `/v1/me/models`: an older relay is asked the plain menu
#: from then on, so the shelf probe costs one request per process, not one per phrase
_no_shelf: set[str] = set()


def forget_menu(base: str = "", kind: str = "") -> None:
    """Drop what was learned about the relay's audio models: one relay, one kind, or all
    (a test, a new key, a menu that no longer names the model)."""
    if not base and not kind:
        _menus.clear()
        _no_shelf.clear()
        return
    for slot in list(_menus):
        relay, _, held_kind = slot.rpartition("|")
        if (not base or relay == base) and (not kind or held_kind == kind):
            _menus.pop(slot, None)


async def _menu(
    base: str,
    key: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[list[dict], dict]:
    """The account's menu and its shelf, as the relay sends them (`/v1/me/models`).

    The shelf is what the person picked for the account; an older relay that does not know
    the endpoint answers 404, and the plain menu (`/v1/models`) is read instead."""
    headers = {"Authorization": f"Bearer {key}"}
    try:
        async with httpx.AsyncClient(transport=transport, timeout=20.0) as client:
            if base in _no_shelf:
                response = await client.get(f"{base}/v1/models", headers=headers)
            else:
                response = await client.get(f"{base}/v1/me/models", headers=headers)
                if response.status_code == 404:
                    _no_shelf.add(base)
                    response = await client.get(f"{base}/v1/models", headers=headers)
    except httpx.HTTPError as exc:
        raise VoiceError(f"the relay did not answer: {type(exc).__name__}") from None
    if response.status_code >= 400:
        raise VoiceError(f"the relay answered {response.status_code}")
    try:
        said = response.json() or {}
    except ValueError:
        raise VoiceError("the relay sent no model menu") from None
    if isinstance(said, dict) and isinstance(said.get("models"), list):
        shelf = said.get("shelf") if isinstance(said.get("shelf"), dict) else {}
        return [row for row in said["models"] if isinstance(row, dict)], shelf
    data = said.get("data") if isinstance(said, dict) else None
    return ([row for row in data if isinstance(row, dict)] if isinstance(data, list) else []), {}


def _audio_kind_of(entry: dict) -> str:
    """What this menu row can do: `speech` or `transcribe`. Three shapes answer it — the
    plain menu (`nanomuse.audio_kind`), the cabinet's catalogue (`audio.kind`) and an
    older relay's flat `audio_kind`."""
    about = entry.get("nanomuse") if isinstance(entry.get("nanomuse"), dict) else {}
    voice = entry.get("audio") if isinstance(entry.get("audio"), dict) else {}
    return str(entry.get("audio_kind") or about.get("audio_kind") or voice.get("kind") or "")


async def _pick(
    base: str,
    key: str,
    kind: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """The model this account keeps for the job, or the first one of that kind.

    The shelf wins, as it does in the picture, clip and sound tools: one account should
    not hear one voice and draw with another model. The answer is kept for
    :data:`MENU_TTL_S` and dropped on a 404 from the audio route."""
    slot = f"{base}|{kind}"
    held = _menus.get(slot)
    if held and time.monotonic() - held[1] < MENU_TTL_S:
        return held[0]
    rows, shelf = await _menu(base, key, transport)
    fits = [
        row
        for row in rows
        if row.get("kind") == "audio"
        and _audio_kind_of(row) == kind
        and (row.get("nanomuse") or {}).get("available") is not False
        and row.get("available", True) is not False
    ]
    if not fits:
        raise VoiceError(f"the menu has no model that can {kind}")
    kept = [str(x) for x in ((shelf.get("enabled") or {}).get("audio") or [])]
    chosen = str((shelf.get("default") or {}).get("audio") or "")
    pick = ""
    if chosen:
        pick = next((str(row.get("id") or "") for row in fits if str(row.get("id") or "") == chosen), "")
    if not pick:
        pick = next((str(row.get("id") or "") for row in fits if str(row.get("id") or "") in kept), "")
    if not pick:
        pick = str(fits[0].get("id") or "")
    if not pick:
        raise VoiceError(f"the menu has no model that can {kind}")
    _menus[slot] = (pick, time.monotonic())
    return pick


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
    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient(transport=transport, timeout=HEAR_TIMEOUT_S) as client:
                response = await client.post(
                    f"{base}/v1/audio/transcriptions", data=form, files=files, headers=headers
                )
        except httpx.HTTPError as exc:
            raise VoiceError(f"the relay did not answer: {type(exc).__name__}") from None
        if response.status_code == 404 and not model and attempt == 1:
            # the model the menu gave us is gone (a plan, a rotation): ask once more
            forget_menu(base, HEAR)
            chosen = await _pick(base, key, HEAR, transport)
            form["model"] = chosen
            continue
        break
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
    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient(transport=transport, timeout=SAY_TIMEOUT_S) as client:
                response = await client.post(f"{base}/v1/audio/speech", json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise VoiceError(f"the relay did not answer: {type(exc).__name__}") from None
        if response.status_code == 404 and not model and attempt == 1:
            forget_menu(base, SAY)
            chosen = await _pick(base, key, SAY, transport)
            payload["model"] = chosen
            continue
        break
    if response.status_code >= 400:
        logger.warning("voice: the relay refused to say it (HTTP {})", response.status_code)
        raise VoiceError(f"the relay answered {response.status_code}")
    if not response.content:
        raise VoiceError("the relay sent no sound")
    mime = (response.headers.get("content-type") or "audio/ogg").split(";")[0].strip()
    return response.content, mime


__all__ = ["HEAR", "SAY", "TEXT_LIMIT", "VoiceError", "forget_menu", "hear", "say"]
