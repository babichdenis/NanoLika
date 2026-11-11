"""No shared-bot action may spend or store through the installation's account."""

from __future__ import annotations

import pytest

from nanomuse.avatar.studio import AvatarStudio
from nanomuse.config import SentinelSettings
from nanomuse.schema import Function, ToolCall, ToolResult
from nanomuse.sentinel import AuditLog, Sentinel
from nanomuse.tools.base import BaseTool
from nanomuse.ui import HeadlessUI


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["picture", "video", "media", "beatapi", "share_file", "models"])
async def test_shared_bot_does_not_call_installation_account(tmp_path, monkeypatch, name):
    class Tool(BaseTool):
        description: str = "test"
        parameters: dict = {"type": "object", "properties": {}}

        async def execute(self, **kwargs):
            return ToolResult(output="installation-account-used")

    gate = Sentinel(SentinelSettings(), AuditLog(tmp_path / "audit"), HeadlessUI(approve=True))
    tool = Tool(name=name)
    call = ToolCall(function=Function(name=name, arguments="{}"))
    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    result = await gate.guard(call, tool)
    assert not result.ok and "аккаунт" in (result.error or "")
    monkeypatch.delenv("NANOMUSE_SHARED_CLOUD_BOT", raising=False)
    assert (await gate.guard(call, tool)).output == "installation-account-used"


def test_shared_avatar_studio_never_uses_installation_credentials(monkeypatch):
    # No service object is required: checking the guard happens before settings/vault.
    studio = AvatarStudio.__new__(AvatarStudio)
    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    assert studio.endpoint() is None
    assert "вашего аккаунта" in studio.unavailable_message("ru")
