"""A picture for the model: the relay draws it under the account's own Cloud key.

The desktop draws with its own plugin and the cabinet has its own button; the bot and the
CLI had no way at all, so "нарисуй кота" in a chat ended with an honest "I cannot". This
tool is that missing half: it asks the relay's `/v1/images/generations`, writes the PNG
into the workspace, and the channel sends the file it sees there — the same artifact
path any other tool's file takes.

The model and the size come from the shelf, not from the dialogue: whatever the person
chose in Settings → Models is what draws.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nanomuse.cloud import CloudError
from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment
from nanomuse.tools.office import resolve_inside

#: what the relay understands; a stray "1024*1024" is the same request
SIZE = re.compile(r"^\d{3,4}\s*[x*×]\s*\d{3,4}$")


class Picture(BaseTool):
    name: str = "picture"
    description: str = (
        "Draw one picture through the account's NanoLika Cloud key and save it in the "
        "workspace, so the chat shows it. `prompt` says what to draw, in the person's own "
        "words; `size` is optional (1024x1024 by default). The model is the one the account "
        "keeps for pictures in its models shelf; `model` overrides it when the person named "
        "another. Use this instead of describing the picture in words."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "What to draw"},
            "size": {"type": "string", "description": "1024x1024, 1024x1536, 1536x1024"},
            "model": {"type": "string", "description": "A picture model id, optional"},
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
            summary="picture",
            warnings=["the prompt goes to the relay and is charged as one picture"],
        )

    async def _model(self, client: Any, wanted: str) -> str:
        """The picture model the account keeps, or the first one its menu offers."""
        if wanted.strip():
            return wanted.strip()
        shelf: dict[str, Any] = {}
        try:
            me = await client.me_models()
            shelf = (me.get("shelf") or {}) if isinstance(me, dict) else {}
        except (CloudError, AttributeError):
            shelf = {}
        default = ((shelf.get("default") or {}) if isinstance(shelf, dict) else {}).get("image")
        if isinstance(default, str) and default:
            return default
        for row in await client.models():
            facts = row.get("nanomuse") if isinstance(row.get("nanomuse"), dict) else row
            if str(facts.get("kind") or "") == "image" and facts.get("available") is not False:
                return str(row.get("id") or "")
        raise CloudError(404, "model_not_offered", "This account has no model that draws pictures")

    async def execute(
        self,
        prompt: str = "",
        size: str = "",
        model: str = "",
        **_: Any,
    ) -> ToolResult:
        said = prompt.strip()
        if not said:
            return ToolResult.fail("picture needs the prompt: what to draw")
        client = self.cloud() if self.cloud is not None else None
        if client is None:
            return ToolResult.fail("Sign in to NanoLika Cloud first.")
        wanted = size.strip()
        if wanted and not SIZE.match(wanted):
            return ToolResult.fail("size is like 1024x1024")
        shape = wanted.replace("*", "x").replace("×", "x").replace(" ", "") if wanted else ""
        try:
            chosen = await self._model(client, model)
            png, accounting = await client.image(chosen, said, size=shape)
        except CloudError as exc:
            # the code says why (insufficient_funds, model_not_offered); the message alone
            # reads like the network had a bad day
            said = f"{exc.message} ({exc.code})" if exc.code and exc.code not in exc.message else exc.message
            return ToolResult.fail(f"Picture failed: {said}")
        if not png:
            return ToolResult.fail("The relay returned no picture")
        rel = Path("pictures") / f"{uuid.uuid4().hex[:12]}.png"
        target = self._resolve(str(rel), must_exist=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(png)
        bits = [f"Picture saved to {rel.as_posix()}"]
        charged = accounting.get("charged") if isinstance(accounting, dict) else None
        if charged:
            bits.append(f"charged {charged}")
        return ToolResult(output=", ".join(bits) + f" ({chosen})")


__all__ = ["Picture", "SIZE"]
