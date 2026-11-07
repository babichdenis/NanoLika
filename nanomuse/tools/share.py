"""A stable link for a file the agent made, so a phone or another device can open it.

The chat sends the file itself, but a document made here lives in this runtime: synced
conversations carry text only. The link is what the other devices get: it is the account's
own storage (the relay), it does not expire, and the desktop app shows it as an ordinary
link in the conversation.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from nanomuse.mime import file_mime
from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment
from nanomuse.tools.office import resolve_inside

#: the storage's own ceiling for one file
LIMIT = 50 * 1024 * 1024


class ShareFile(BaseTool):
    name: str = "share_file"
    description: str = (
        "Put a file you made into the account's own storage and get a stable link. Use it "
        "for every document, spreadsheet or archive the person should be able to open on a "
        "phone or another device, and put the link in the reply as `[name](link)`: the chat "
        "shows the file itself, and the link is what the other devices can open."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The file, relative to this conversation workspace",
            },
        },
        "required": ["path"],
    }
    risk: RiskLevel = RiskLevel.MODERATE
    workspace: Path = Path(".")
    extra_roots: list[Path] = []
    cloud: Callable[[], Any] | None = None

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        # No warning on purpose: the file is the person's own, it goes to the account's own
        # storage, and a card here would ask about a document they just requested.
        return CallAssessment(
            risk=RiskLevel.MODERATE,
            egress=True,
            egress_target="cloud",
            summary="share a file with the other devices",
        )

    async def execute(self, path: str = "", **_: Any) -> ToolResult:
        raw = path.strip()
        if not raw:
            return ToolResult.fail("share_file needs the path of the file")
        try:
            target = resolve_inside(
                self.workspace, self.extra_roots, raw, must_exist=True, write=False
            )
        except (ValueError, FileNotFoundError, PermissionError) as exc:
            return ToolResult.fail(str(exc))
        if not target.is_file():
            return ToolResult.fail(f"{raw} is not a file")
        size = target.stat().st_size
        if size > LIMIT:
            return ToolResult.fail(
                f"{target.name} is {size} bytes; the storage takes up to {LIMIT}"
            )
        client = self.cloud() if self.cloud is not None else None
        if client is None:
            return ToolResult.fail(
                "Sign in to NanoLika Cloud first: there is no storage to put the file in."
            )
        mime = file_mime(target.name)
        try:
            link = await client.upload(target.name, target.read_bytes(), mime)
        except Exception as exc:  # noqa: BLE001 — the chat can still send the file itself
            return ToolResult.fail(f"the file did not reach the storage: {exc}")
        if not link:
            return ToolResult.fail(
                "the storage answered no link; the chat can still send the file itself"
            )
        return ToolResult(
            output=f"link {link}\n({target.name}, {size} bytes), put it in the reply as "
            f"[{target.name}]({link})"
        )


__all__ = ["ShareFile"]
