"""Conversation access bounds, selected only by the authenticated owner menu."""

from pathlib import Path
from typing import Any

from nanomuse.sentinel.policy import Decision
from nanomuse.tools.base import BaseTool, CallAssessment

# Unknown tools never gain a read or workspace-write exemption.
READ_ACTIONS = {"read", "list", "search"}
READ_TOOLS = {"web_search", "web_fetch", "search", "fetch", "time", "terminate"}


def scoped_decision(
    mode: str, tool: BaseTool, args: dict[str, Any], assessment: CallAssessment, original: Decision
) -> Decision:
    if original == Decision.DENY:
        return original
    if mode == "full-access":
        return Decision.ALLOW
    if mode not in {"read-only", "workspace-write"}:
        return original
    if tool.name == "files":
        action = args.get("action")
        if action in READ_ACTIONS:
            return original
        if mode == "workspace-write" and action in {"write", "append"}:
            workspace = getattr(tool, "workspace", None)
            if workspace is not None:
                root = Path(workspace).resolve()
                target = (root / str(args.get("path") or ".")).resolve()
                if target == root or root in target.parents:
                    return Decision.ALLOW
        return Decision.DENY
    if tool.name in READ_TOOLS and not assessment.warnings:
        return original
    # No arbitrary shell/Python/browser actions can masquerade as a read.
    # Workspace mode asks for each other kind of action; read-only refuses it.
    return Decision.DENY if mode == "read-only" else Decision.ASK
