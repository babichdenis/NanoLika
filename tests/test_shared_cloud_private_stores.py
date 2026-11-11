"""Shared bot must not disclose its one process-global personal-data store."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from nanomuse.agent import MuseAgent
from nanomuse.config import SentinelSettings, Settings
from nanomuse.goals import GoalStore
from nanomuse.llm import MockLLM
from nanomuse.schema import Function, ToolCall, ToolResult
from nanomuse.sentinel import AuditLog, Sentinel
from nanomuse.server.service import MuseService
from nanomuse.tools import Goals, ToolCollection
from nanomuse.tools.base import BaseTool
from nanomuse.ui import HeadlessUI


@pytest.mark.asyncio
async def test_customer_cannot_read_other_customers_goal_via_tool_or_prompt(tmp_path, monkeypatch):
    ui = HeadlessUI(approve=False)
    store = GoalStore(tmp_path / "goals.db")
    gate = Sentinel(SentinelSettings(), AuditLog(tmp_path / "audit"), ui)
    gate.channel_mode = lambda _thread: "full-access"
    tool = Goals(store=store)
    tools = ToolCollection(tool)

    async def invoke(thread, action, **kwargs):
        call = ToolCall(function=Function(name="goals", arguments=json.dumps({"action": action, **kwargs})))
        token = gate.begin_task(conversation=thread)
        try:
            return await gate.guard(call, tool)
        finally:
            gate.end_task(token)

    # The personal mode demonstrates the pre-existing shared-store leak in two fake chats.
    monkeypatch.delenv("NANOMUSE_SHARED_CLOUD_BOT", raising=False)
    assert (await invoke("customer-a", "create", title="private-goal-marker")).ok
    assert "private-goal-marker" in (await invoke("customer-b", "list")).output
    settings = Settings()
    settings.agent.user_profile = "private-profile-marker"
    agent = MuseAgent(settings, MockLLM([]), tools, gate, ui, gate.audit, goals=store)
    assert "private-goal-marker" in agent.build_system_prompt("hello")
    assert "private-profile-marker" in agent.build_system_prompt("hello")
    assert "temporarily unavailable" not in agent.sandbox_note()

    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    result = await invoke("customer-b", "list")
    assert not result.ok and "private-goal-marker" not in result.for_model()
    assert "private-goal-marker" not in agent.build_system_prompt("hello")
    assert "private-profile-marker" not in agent.build_system_prompt("hello")
    assert "temporarily unavailable" in agent.sandbox_note()


@pytest.mark.asyncio
async def test_shared_bot_does_not_start_legacy_global_scheduler(monkeypatch, tmp_path):
    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    feed = tmp_path / "feed_posts.json"
    feed.write_text('{"posts": [{"title": "existing"}]}')

    def never_called():
        raise AssertionError("global personal scheduler must not run for the shared bot")

    await MuseService._goal_scheduler(SimpleNamespace(schedule_next_pass=never_called))
    assert feed.read_text() == '{"posts": [{"title": "existing"}]}'


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [
    "goals", "reminders", "triggers", "contacts", "calendar", "read_emails", "send_email",
    "browser", "bot_projects",
])
async def test_shared_bot_refuses_global_personal_tools_before_execution(tmp_path, monkeypatch, name):
    class Tool(BaseTool):
        description: str = "test"
        parameters: dict = {"type": "object", "properties": {}}

        async def execute(self, **kwargs):
            return ToolResult(output="executed")

    gate = Sentinel(SentinelSettings(), AuditLog(tmp_path / "audit"), HeadlessUI(approve=True))
    tool = Tool(name=name)
    request = ToolCall(function=Function(name=name, arguments="{}"))
    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    result = await gate.guard(request, tool)
    assert not result.ok and "хранилище" in (result.error or "")

    monkeypatch.delenv("NANOMUSE_SHARED_CLOUD_BOT", raising=False)
    assert (await gate.guard(request, tool)).output == "executed"
