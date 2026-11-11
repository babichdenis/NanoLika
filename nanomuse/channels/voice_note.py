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

import hashlib
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

#: How long an account's old-relay fallback is remembered. Model choices themselves are
#: fetched anew: another device may have changed the person's shelf or tariff.
MENU_TTL_S = 600.0
MAX_FALLBACK_ENTRIES = 512


class VoiceError(RuntimeError):
    """The relay could not hear or say it; the caller keeps the turn as it was."""


#: Old-relay fallback is keyed by a one-way digest of the specific credential: one
#: account's 404 must not hide another account's shelf, and raw keys are not retained.
_no_shelf: dict[tuple[str, str], float] = {}


def forget_menu(base: str = "", kind: str = "", *, key: str = "") -> None:
    """Forget old-relay fallbacks, for one relay or all relays.

    `kind` remains accepted for existing callers; the fallback endpoint is shared by
    both kinds, while model choices are not cached.
    """
    if not base and not kind and not key:
        _no_shelf.clear()
        return
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest() if key else ""
    for slot in list(_no_shelf):
        if (not base or slot[0] == base) and (not digest or slot[1] == digest):
            _no_shelf.pop(slot, None)


async def _menu(
    base: str,
    key: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[list[dict], dict]:
    """The account's menu and its shelf, as the relay sends them (`/v1/me/models`).

    The shelf is what the person picked for the account; an older relay that does not know
    the endpoint answers 404, and the plain menu (`/v1/models`) is read instead."""
    headers = {"Authorization": f"Bearer {key}"}
    slot = (base, hashlib.sha256(key.encode("utf-8")).hexdigest())
    previous = _no_shelf.get(slot)
    fallback = previous is not None and time.monotonic() - previous < MENU_TTL_S
    if previous is not None and not fallback:
        _no_shelf.pop(slot, None)
    try:
        async with httpx.AsyncClient(transport=transport, timeout=20.0) as client:
            if fallback:
                response = await client.get(f"{base}/v1/models", headers=headers)
            else:
                response = await client.get(f"{base}/v1/me/models", headers=headers)
                if response.status_code == 404:
                    if len(_no_shelf) >= MAX_FALLBACK_ENTRIES and slot not in _no_shelf:
                        _no_shelf.pop(min(_no_shelf, key=_no_shelf.get), None)
                    _no_shelf[slot] = time.monotonic()
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
        shelf = said.get("shelf")
        if not isinstance(shelf, dict):
            shelf = {}
        return [row for row in said["models"] if isinstance(row, dict)], shelf
    data = said.get("data") if isinstance(said, dict) else None
    return ([row for row in data if isinstance(row, dict)] if isinstance(data, list) else []), {}


def _audio_kind_of(entry: dict) -> str:
    """What this menu row can do: `speech` or `transcribe`. Three shapes answer it — the
    plain menu (`nanomuse.audio_kind`), the cabinet's catalogue (`audio.kind`) and an
    older relay's flat `audio_kind`."""
    about = entry.get("nanomuse")
    if not isinstance(about, dict):
        about = {}
    voice = entry.get("audio")
    if not isinstance(voice, dict):
        voice = {}
    return str(entry.get("audio_kind") or about.get("audio_kind") or voice.get("kind") or "")


async def _pick(
    base: str,
    key: str,
    kind: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """The model this account keeps for the job, or the first one of that kind.

    The shelf wins, as it does in the picture, clip and sound tools: each request reads
    this account's current shelf and plan instead of using a stale or shared selection."""
    rows, shelf = await _menu(base, key, transport)
    fits = [
        row
        for row in rows
        if row.get("kind") == "audio"
        and _audio_kind_of(row) == kind
        and row.get("plan_locked") is not True
        and (row.get("nanomuse") or {}).get("available") is not False
        and row.get("available", True) is not False
    ]
    if not fits:
        raise VoiceError(f"the menu has no model that can {kind}")
    kept = [str(x) for x in ((shelf.get("enabled") or {}).get("audio") or [])]
    chosen = str((shelf.get("default") or {}).get("audio") or "")
    pick = ""
    if chosen:
        pick = next(
            (str(row.get("id") or "") for row in fits if str(row.get("id") or "") == chosen), ""
        )
    if not pick:
        pick = next(
            (str(row.get("id") or "") for row in fits if str(row.get("id") or "") in kept), ""
        )
    if not pick:
        pick = str(fits[0].get("id") or "")
    if not pick:
        raise VoiceError(f"the menu has no model that can {kind}")
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
            forget_menu(base, HEAR, key=key)
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
    payload: dict[str, object] = {
        "model": chosen,
        "input": body[:TEXT_LIMIT],
        "response_format": fmt,
    }
    if voice:
        payload["voice"] = voice
    headers = {"Authorization": f"Bearer {key}"}
    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient(transport=transport, timeout=SAY_TIMEOUT_S) as client:
                response = await client.post(
                    f"{base}/v1/audio/speech", json=payload, headers=headers
                )
        except httpx.HTTPError as exc:
            raise VoiceError(f"the relay did not answer: {type(exc).__name__}") from None
        if response.status_code == 404 and not model and attempt == 1:
            forget_menu(base, SAY, key=key)
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
