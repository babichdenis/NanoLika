"""The shared bot must never execute customer code in its unisolated process."""

from __future__ import annotations

import json
import shlex

import pytest

from nanomuse.config import SentinelSettings
from nanomuse.schema import Function, ToolCall
from nanomuse.sentinel import AuditLog, Sentinel
from nanomuse.tools.coding_tool import CodingAgents
from nanomuse.tools.shell import PythonExecute, Shell
from nanomuse.ui import HeadlessUI
from nanomuse.workspace_context import workspace_context


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["python", "shell"])
async def test_shared_bot_cannot_read_other_workspace_even_with_full_access(tmp_path, monkeypatch, kind):
    own = tmp_path / "own"
    other = tmp_path / "other"
    own.mkdir()
    other.mkdir()
    secret = other / "marker.txt"
    secret.write_text("test-other-customer-marker")
    gate = Sentinel(SentinelSettings(), AuditLog(tmp_path / "audit.jsonl"), HeadlessUI(approve=False))
    gate.channel_mode = lambda _thread: "full-access"
    tool = PythonExecute(workspace=own) if kind == "python" else Shell(workspace=own)
    args = ({"code": f"from pathlib import Path; print(Path({str(secret)!r}).read_text())"}
            if kind == "python" else {"command": f"cat {shlex.quote(str(secret))}"})
    call = ToolCall(function=Function(name=tool.name, arguments=json.dumps(args)))

    async def invoke():
        token = gate.begin_task(conversation="test-telegram-topic")
        try:
            with workspace_context("test-telegram-topic", own):
                return await gate.guard(call, tool)
        finally:
            gate.end_task(token)

    # On a personal installation, full access intentionally retains local execution.
    monkeypatch.delenv("NANOMUSE_SHARED_CLOUD_BOT", raising=False)
    desktop = await invoke()
    assert desktop.ok and "test-other-customer-marker" in desktop.output

    # Exactly the same user-channel path must fail closed on the shared service.
    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    result = await invoke()
    assert not result.ok
    assert "test-other-customer-marker" not in result.for_model()
    assert "изолирован" in (result.error or "")


@pytest.mark.asyncio
async def test_shared_bot_does_not_call_coding_agent_service(monkeypatch):
    class NeverCalled:
        def agents(self):
            raise AssertionError("shared service must not inspect the host's coding agents")

    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    result = await CodingAgents(coding=NeverCalled()).execute(action="agents")
    assert not result.ok
    assert "изолирован" in (result.error or "")
