"""A short clip for the model: the relay makes it under the account's own Cloud key.

Pictures have had their tool since 0.26 and clips had none: the cabinet shows a video
model, the relay has the whole asynchronous route, but a chat could only answer "I
cannot". This tool is the missing half — submit, wait for the provider's task, keep the
file, and the channel sends it as any other artifact.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nanomuse.cloud import FUNDS_CODES, FUNDS_MESSAGE, CloudError
from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment
from nanomuse.tools.office import resolve_inside

#: The provider's own aspects; anything else is refused by it rather than guessed here.
SIZES = "1280x720, 720x1280, 1024x1024, 1920x1080"
RESOLUTIONS = ("480P", "720P", "1080P")


class Video(BaseTool):
    name: str = "video"
    description: str = (
        "Make one short clip through the account's NanoLika Cloud key and save it in the "
        "workspace, so the chat shows it. `prompt` describes the scene and the motion; "
        "`seconds` is the length (5 by default), `size` an aspect like 1280x720, "
        "`resolution` 480P, 720P or 1080P. The model is the account's video model from its "
        "models shelf; `model` names another when the person asked for one. Check the price "
        "with the `models` tool (action=estimate, clips=1) when it matters. A clip takes "
        "minutes: say so before starting, and do not make a second one while the first runs."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "The scene and what moves"},
            "model": {"type": "string", "description": "A video model id, optional"},
            "seconds": {"type": "integer", "description": "5 by default"},
            "size": {"type": "string", "description": SIZES},
            "resolution": {"type": "string", "enum": list(RESOLUTIONS)},
        },
        "required": ["prompt"],
    }
    risk: RiskLevel = RiskLevel.MODERATE
    workspace: Path = Path(".")
    extra_roots: list[Path] = []
    cloud: Callable[[], Any] | None = None

    def _resolve(self, raw: str, must_exist: bool = True) -> Path:
        return resolve_inside(self.workspace, self.extra_roots, raw, must_exist, write=not must_exist)

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        return CallAssessment(
            risk=RiskLevel.MODERATE,
            egress=True,
            egress_target="cloud",
            summary="video",
        )

    async def _model(self, client: Any, wanted: str) -> str:
        """The video model the account keeps, or the first one its menu allows."""
        if wanted.strip():
            return wanted.strip()
        shelf: dict[str, Any] = {}
        try:
            menu = await client.me_models()
            shelf = (menu.get("shelf") or {}) if isinstance(menu, dict) else {}
        except (CloudError, AttributeError):
            shelf = {}
        default = ((shelf.get("default") or {}) if isinstance(shelf, dict) else {}).get("video")
        if isinstance(default, str) and default:
            return default
        for row in await client.models():
            facts = row.get("nanomuse") if isinstance(row.get("nanomuse"), dict) else row
            kind = row.get("kind") or facts.get("kind")
            if str(kind or "") == "video" and row.get("available", True) is not False:
                return str(row.get("id") or "")
        raise CloudError(404, "model_not_offered", "This account has no model that makes clips")

    async def execute(
        self,
        prompt: str = "",
        model: str = "",
        seconds: int = 5,
        size: str = "",
        resolution: str = "",
        **_: Any,
    ) -> ToolResult:
        said = prompt.strip()
        if not said:
            return ToolResult.fail("video needs the prompt: what happens in the clip")
        client = self.cloud() if self.cloud is not None else None
        if client is None:
            return ToolResult.fail("Sign in to NanoLika Cloud first.")
        length = max(1, min(int(seconds or 5), 30))
        shape = size.strip().replace("*", "x").replace("×", "x")
        grade = resolution.strip().upper()
        if grade and grade not in RESOLUTIONS:
            return ToolResult.fail("resolution is 480P, 720P or 1080P")
        try:
            chosen = await self._model(client, model)
            data, accounting = await client.video(chosen, said, seconds=length, size=shape, resolution=grade)
        except CloudError as exc:
            if exc.code in FUNDS_CODES:
                return ToolResult.fail(FUNDS_MESSAGE)
            message = f"{exc.message} ({exc.code})" if exc.code and exc.code not in exc.message else exc.message
            return ToolResult.fail(f"Clip failed: {message}")
        if not data:
            return ToolResult.fail("The relay returned no clip")
        rel = Path("videos") / f"{uuid.uuid4().hex[:12]}.mp4"
        target = self._resolve(str(rel), must_exist=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        bits = [f"Clip saved to {rel.as_posix()}", f"{length} s"]
        # the price was quoted before the call and the person was told it; the relay's
        # `charged` is in its own units and has no place in a chat answer
        return ToolResult(output=", ".join(bits) + f" ({len(data)} bytes)")
