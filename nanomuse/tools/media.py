"""Звук для модели: озвучить текст или распознать запись.

Сам звук делает релей (`/v1/audio/speech` и `/v1/audio/transcriptions`) под ключом
аккаунта, поэтому здесь нет ни ключей провайдеров, ни выбора поставщика. Файл,
который сказала Лика, ложится в рабочую папку и остаётся доступным под своим
путём; запись для распознавания читается только из рабочей папки.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nanomuse.cloud import CloudError
from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment
from nanomuse.tools.office import resolve_inside

SPEECH_FORMATS = {"mp3": "audio/mpeg", "wav": "audio/wav", "opus": "audio/ogg", "aac": "audio/aac", "flac": "audio/flac"}
RECORDING_SUFFIX = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".webm": "audio/webm",
}


class Media(BaseTool):
    name: str = "media"
    description: str = (
        "Sound through the account's NanoLika Cloud key. action=`speech` speaks `text` "
        "aloud and saves the file in the workspace (voice, format, speed); action="
        "`transcribe` turns a recording inside the workspace into text (path, language). "
        "The result names the file or the words and what the relay charged. Use this "
        "instead of calling a provider's audio API yourself."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["speech", "transcribe"]},
            "text": {"type": "string", "description": "What to say (speech)"},
            "voice": {"type": "string", "description": "The provider's voice id, optional"},
            "format": {"type": "string", "enum": list(SPEECH_FORMATS), "description": "mp3 by default"},
            "speed": {"type": "number", "description": "0.25 to 4, optional"},
            "path": {"type": "string", "description": "The recording to transcribe, inside the workspace"},
            "language": {"type": "string", "description": "Spoken language, optional"},
        },
        "required": ["action"],
    }
    risk: RiskLevel = RiskLevel.MODERATE
    workspace: Path = Path(".")
    extra_roots: list[Path] = []
    cloud: Callable[[], Any] | None = None

    def _resolve(self, raw: str, must_exist: bool = True) -> Path:
        return resolve_inside(self.workspace, self.extra_roots, raw, must_exist, write=not must_exist)

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        action = str(args.get("action") or "")
        # Speaking a text or hearing a recording is the request itself; a warning here only
        # added a card to a chat where the person had already asked for the sound.
        return CallAssessment(
            risk=RiskLevel.MODERATE,
            egress=action == "speech",
            egress_target="cloud",
            summary=f"media {action}",
        )

    async def _model(self, kind: str) -> str:
        """Первая модель аккаунта для этого дела: голос или распознавание."""
        client = self.cloud() if self.cloud is not None else None
        if client is None:
            raise CloudError(401, "signed_out", "Sign in to NanoLika Cloud first.")
        for row in await client.models():
            facts = row.get("nanomuse") if isinstance(row.get("nanomuse"), dict) else row
            if str(facts.get("audio_kind") or "") == kind:
                return str(row.get("id") or "")
        raise CloudError(404, "model_not_offered", f"This relay offers no model that can {kind}")

    async def execute(
        self,
        action: str = "",
        text: str = "",
        voice: str = "",
        format: str = "mp3",
        speed: float | None = None,
        path: str = "",
        language: str = "",
        **_: Any,
    ) -> ToolResult:
        client = self.cloud() if self.cloud is not None else None
        if client is None:
            return ToolResult.fail("Sign in to NanoLika Cloud first.")
        if action == "speech":
            if not text.strip():
                return ToolResult.fail("media speech needs the text to say")
            fmt = format if format in SPEECH_FORMATS else "mp3"
            try:
                model = await self._model("speech")
                sound, media_type, headers = await client.speech(
                    model, text.strip(), voice=voice, fmt=fmt, speed=speed
                )
            except CloudError as exc:
                return ToolResult.fail(f"Speech failed: {exc.message}")
            if not sound:
                return ToolResult.fail("The relay returned no sound")
            rel = Path("media") / f"{uuid.uuid4().hex[:12]}.{fmt}"
            target = self._resolve(str(rel), must_exist=False)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(sound)
            charged = headers.get("x-nanomuse-charged", "")
            seconds = headers.get("x-nanomuse-seconds", "")
            bits = [f"Speech saved to {rel.as_posix()}"]
            if seconds:
                bits.append(f"{seconds} s")
            if charged:
                bits.append(f"charged {charged}")
            return ToolResult(output=", ".join(bits) + f" ({media_type})")
        if action == "transcribe":
            if not path.strip():
                return ToolResult.fail("media transcribe needs the recording's path")
            try:
                source = self._resolve(path)
            except (FileNotFoundError, PermissionError) as exc:
                return ToolResult.fail(str(exc))
            suffix = source.suffix.lower()
            mime = RECORDING_SUFFIX.get(suffix, "audio/webm")
            try:
                model = await self._model("transcribe")
                said = await client.transcribe(model, source.name, source.read_bytes(), mime, language=language)
            except CloudError as exc:
                return ToolResult.fail(f"Transcription failed: {exc.message}")
            words = str(said.get("text") or "").strip()
            if not words:
                return ToolResult.fail("The relay heard no words")
            seconds = (said.get("nanomuse") or {}).get("seconds")
            tail = f" ({seconds} s)" if seconds else ""
            return ToolResult(output=f"{words}{tail}")
        return ToolResult.fail("media action is `speech` or `transcribe`")
