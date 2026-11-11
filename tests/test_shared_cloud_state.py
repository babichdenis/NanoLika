"""Shared bot customers cannot change globally shared skills or install code."""

from __future__ import annotations

import json

import pytest

from nanomuse.config import SentinelSettings, SkillsSettings
from nanomuse.schema import Function, ToolCall
from nanomuse.sentinel import AuditLog, Sentinel
from nanomuse.skills import SkillLibrary
from nanomuse.tools.skills_tool import Skills
from nanomuse.tools.store import Store
from nanomuse.ui import HeadlessUI


@pytest.mark.asyncio
async def test_shared_bot_blocks_mutating_global_skills_even_in_full_access(tmp_path, monkeypatch):
    builtin = tmp_path / "builtin" / "standard"
    builtin.mkdir(parents=True)
    (builtin / "SKILL.md").write_text(
        "---\nname: standard\ndescription: Standard skill\n---\n\nStandard instructions\n"
    )
    lib = SkillLibrary(SkillsSettings(), own_dir=tmp_path / "skills", builtin_dir=builtin.parent)
    tool = Skills(library=lib)
    gate = Sentinel(SentinelSettings(), AuditLog(tmp_path / "audit"), HeadlessUI(approve=False))
    gate.channel_mode = lambda _: "full-access"

    async def call(conversation, action, **kw):
        request = ToolCall(function=Function(name="skills", arguments=json.dumps({"action": action, **kw})))
        token = gate.begin_task(conversation=conversation)
        try:
            return await gate.guard(request, tool)
        finally:
            gate.end_task(token)

    # Reproduce the original bug: both customer threads see one shared library.
    monkeypatch.delenv("NANOMUSE_SHARED_CLOUD_BOT", raising=False)
    saved = await call("customer-a", "save", name="test-shared", description="test", instructions="from customer A")
    assert saved.ok
    assert "from customer A" in (await call("customer-b", "use", name="test-shared")).output

    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    # Previously written private skills must be absent everywhere: the model's
    # prompt, explicit slash commands and both public tool entry points.
    assert "test-shared" not in lib.index()
    assert lib.expand("/test-shared") == "/test-shared"
    assert lib.get("test-shared") is None
    assert not (await call("customer-b", "use", name="test-shared")).ok
    listed = await call("customer-b", "list")
    assert listed.ok and "test-shared" not in listed.output and "standard" in listed.output
    assert "Standard instructions" in (await call("customer-b", "use", name="standard")).output
    assert "Standard instructions" in lib.expand("/standard")
    saved = await call("customer-a", "save", name="test-new", description="test", instructions="private")
    assert not saved.ok
    assert lib.get("test-new") is None
    removed = await call("customer-b", "remove", name="test-shared")
    assert not removed.ok
    # Personal installations continue to expose their own saved skills.
    monkeypatch.delenv("NANOMUSE_SHARED_CLOUD_BOT", raising=False)
    assert "from customer A" in (await call("customer-a", "use", name="test-shared")).output


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["addon", "skill", "mcp"])
async def test_shared_bot_refuses_store_install_before_network_or_changes(monkeypatch, tmp_path, action):
    async def should_not_fetch(*_args, **_kwargs):
        raise AssertionError("shared bot must refuse before fetching or installing")

    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    monkeypatch.setattr(Store, "_fetch_index", should_not_fetch)
    result = await Store(addons_dir=tmp_path).execute(action=action, name="test")
    assert not result.ok
    assert "общ" in (result.error or "").lower()
