"""The shared bot gives each verified account its own personal skill library."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from nanomuse.config import SentinelSettings, SkillsSettings
from nanomuse.memory.scope import memory_scope
from nanomuse.schema import Function, ToolCall
from nanomuse.sentinel import AuditLog, Sentinel
from nanomuse.skills import SkillLibrary
from nanomuse.tools.skills_tool import Skills
from nanomuse.ui import HeadlessUI


@pytest.mark.asyncio
async def test_personal_skills_are_isolated_across_concurrent_accounts(tmp_path, monkeypatch):
    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    builtin = tmp_path / "builtin" / "standard"
    builtin.mkdir(parents=True)
    (builtin / "SKILL.md").write_text(
        "---\nname: standard\ndescription: Standard skill\n---\n\nBuilt-in instructions\n"
    )
    shared = tmp_path / "skills" / "old-private"
    shared.mkdir(parents=True)
    original = "---\nname: old-private\ndescription: old\n---\n\noperator secret\n"
    (shared / "SKILL.md").write_text(original)
    lib = SkillLibrary(SkillsSettings(), own_dir=shared.parent, builtin_dir=builtin.parent)
    tool = Skills(library=lib)
    gate = Sentinel(SentinelSettings(), AuditLog(tmp_path / "audit"), HeadlessUI(approve=False))
    gate.channel_mode = lambda _: "full-access"
    start = asyncio.Event()
    ready = 0

    async def run(scope, secret):
        nonlocal ready
        with memory_scope(scope):
            token = gate.begin_task(conversation=scope)
            try:
                call = ToolCall(function=Function(name="skills", arguments=json.dumps({
                    "action": "save", "name": "mine", "description": secret, "instructions": secret,
                })))
                saved = await gate.guard(call, tool)
                assert saved.ok
                ready += 1
                if ready == 2:
                    start.set()
                await start.wait()
                await asyncio.sleep(0)
                return lib.index(), lib.expand("/mine do"), (await tool.execute(action="use", name="mine")).output
            finally:
                gate.end_task(token)

    first, second = await asyncio.gather(run("account:skills-a", "private alpha"), run("account:skills-b", "private beta"))
    for text in first:
        assert "private alpha" in text and "private beta" not in text and "operator secret" not in text
    for text in second:
        assert "private beta" in text and "private alpha" not in text and "operator secret" not in text
    assert (shared / "SKILL.md").read_text() == original

    # Same account, new library object: data persist; a new account sees only built-ins.
    restarted = SkillLibrary(SkillsSettings(), own_dir=shared.parent, builtin_dir=builtin.parent)
    with memory_scope("account:skills-a"):
        assert "private alpha" in restarted.get("mine").body
        personal = restarted.get("mine").path
        assert personal.stat().st_mode & 0o777 == 0o700
        assert (personal / "SKILL.md").stat().st_mode & 0o777 == 0o600
        assert "Built-in instructions" in restarted.expand("/standard")
        assert restarted.set_enabled("standard", False).enabled is False
    with memory_scope("account:skills-b"):
        assert restarted.get("standard").enabled is True
        assert restarted.remove("mine")
        assert restarted.get("mine") is None
    with memory_scope("account:skills-a"):
        assert restarted.get("mine") is not None
    restarted2 = SkillLibrary(SkillsSettings(), own_dir=shared.parent, builtin_dir=builtin.parent)
    with memory_scope("account:skills-a"):
        assert restarted2.get("standard").enabled is False
    with memory_scope("account:skills-c"):
        assert restarted2.get("mine") is None
        assert restarted2.get("standard").enabled is True


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["installation", "channel:telegram:unknown", "legacy-session:one", "account:"])
async def test_unverified_scope_can_read_builtins_but_cannot_write(tmp_path: Path, monkeypatch, scope):
    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    builtin = tmp_path / "builtin" / "standard"
    builtin.mkdir(parents=True)
    (builtin / "SKILL.md").write_text("---\nname: standard\ndescription: public\n---\n\nbuilt-in\n")
    shared = tmp_path / "skills" / "old"
    shared.mkdir(parents=True)
    (shared / "SKILL.md").write_text("---\nname: old\ndescription: private\n---\n\nsecret\n")
    lib = SkillLibrary(SkillsSettings(), own_dir=shared.parent, builtin_dir=builtin.parent)
    tool = Skills(library=lib)
    with memory_scope(scope):
        assert lib.get("standard") is not None
        assert lib.get("old") is None
        result = await tool.execute(action="save", name="mine", description="x", instructions="secret")
        assert not result.ok and "аккаунт" in result.error
    assert not (tmp_path / "skills-accounts").exists()


@pytest.mark.asyncio
async def test_one_hundred_accounts_do_not_share_skills(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    lib = SkillLibrary(SkillsSettings(), own_dir=tmp_path / "skills", builtin_dir=tmp_path / "builtin")
    tool = Skills(library=lib)

    async def customer(number):
        own = f"personal-{number}"
        with memory_scope(f"account:customer-{number}"):
            saved = await tool.execute(action="save", name="own", description=own, instructions=own)
            assert saved.ok
            await asyncio.sleep(0)
            result = await tool.execute(action="use", name="own")
            assert result.ok and own in result.output
            assert lib.index().splitlines() == [f"- own: {own}"]

    await asyncio.gather(*(customer(number) for number in range(100)))
    assert len(list((tmp_path / "skills-accounts").iterdir())) == 100


def test_personal_skill_cannot_follow_links_to_other_accounts(tmp_path, monkeypatch):
    import hashlib

    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    lib = SkillLibrary(SkillsSettings(), own_dir=tmp_path / "skills", builtin_dir=tmp_path / "builtin")
    with memory_scope("account:target"):
        lib.save("secret", "secret", "private content")
        target = lib.get("secret").path
    digest = hashlib.sha256(b"account:attacker").hexdigest()
    own = tmp_path / "skills-accounts" / digest
    own.mkdir(parents=True)
    (own / "stolen").symlink_to(target, target_is_directory=True)
    with memory_scope("account:attacker"):
        assert lib.get("stolen") is None
        with pytest.raises(PermissionError):
            lib.save("stolen", "description", "replacement")
    with memory_scope("account:target"):
        assert lib.get("secret").body == "private content"


def test_failed_personal_skill_update_preserves_existing_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    lib = SkillLibrary(SkillsSettings(), own_dir=tmp_path / "skills", builtin_dir=tmp_path / "builtin")
    with memory_scope("account:owner"):
        skill = lib.save("mine", "before", "original instructions")
        original = (skill.path / "SKILL.md").read_text()
        replace = Path.replace

        def fail_replace(source, target):
            if Path(target).name == "SKILL.md":
                raise OSError("simulated write failure")
            return replace(source, target)

        with monkeypatch.context() as temporary:
            temporary.setattr(Path, "replace", fail_replace)
            with pytest.raises(OSError, match="simulated"):
                lib.save("mine", "after", "changed instructions")
        assert (skill.path / "SKILL.md").read_text() == original
        assert sorted(path.name for path in skill.path.iterdir()) == ["SKILL.md"]


def test_shared_agent_prompt_shows_only_current_accounts_skills(settings, tmp_path, monkeypatch):
    from test_agent import make_agent

    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    lib = SkillLibrary(SkillsSettings(), own_dir=tmp_path / "skills", builtin_dir=tmp_path / "builtin")
    with memory_scope("account:prompt-a"):
        lib.save("customer-a", "exclusive alpha", "alpha instructions")
    with memory_scope("account:prompt-b"):
        lib.save("customer-b", "exclusive beta", "beta instructions")
    agent, *_ = make_agent(settings, [], extra_tools=[Skills(library=lib)])
    agent.skills = lib
    with memory_scope("account:prompt-a"):
        first = agent.build_system_prompt("hello")
    with memory_scope("account:prompt-b"):
        second = agent.build_system_prompt("hello")
    with memory_scope("channel:unverified"):
        unknown = agent.build_system_prompt("hello")
    assert "exclusive alpha" in first and "exclusive beta" not in first
    assert "exclusive beta" in second and "exclusive alpha" not in second
    assert "exclusive alpha" not in unknown and "exclusive beta" not in unknown
