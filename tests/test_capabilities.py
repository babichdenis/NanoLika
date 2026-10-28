"""The inventory of what the build can do must reach the model, and must not lie.

The case: "watch my mail" ran into "I cannot do mail" while the connector was right there.
An inventory nobody reads does not fix that, so the prompt carries it and the file is
generated from the same registry.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from nanomuse import capabilities

ROOT = Path(__file__).resolve().parents[1]


class FakeTool:
    def __init__(self, name: str, description: str, risk: str = "safe"):
        self.name = name
        self.description = description
        self.risk = risk


class FakeSettings:
    def __init__(self, *, mail: bool, calendar: bool = False, contacts: bool = False):
        from nanomuse.config import CalendarSettings, ContactsSettings, EmailSettings

        self.connectors = type(
            "Connectors",
            (),
            {
                "email": EmailSettings(
                    enabled=mail,
                    imap_host="imap.example.com" if mail else "",
                    address="a@b.c",
                    password="x",
                ),
                "calendar": CalendarSettings(enabled=calendar),
                "contacts": ContactsSettings(enabled=contacts),
            },
        )()


def tools(*names: str) -> list[FakeTool]:
    return [FakeTool(name, f"Does {name} things. And more.") for name in names]


def test_every_loaded_tool_is_listed_somewhere():
    loaded = tools(
        "read_emails", "files", "shell", "web_search", "remember", "store", "something-new"
    )
    data = capabilities.inventory(loaded, FakeSettings(mail=True))
    listed = {tool["name"] for group in data["groups"] for tool in group["tools"]}
    assert listed == {t.name for t in loaded}
    # a tool nobody sorted into a group still appears, under "everything else"
    assert "something-new" in listed


def test_mail_is_described_as_connected_when_it_is():
    data = capabilities.inventory(
        tools("read_emails", "send_email", "triggers", "calendar", "contacts"),
        FakeSettings(mail=True, calendar=True, contacts=True),
    )
    text = capabilities.render_prompt(data)
    assert "`read_emails`" in text
    assert "Not connected" not in text


def test_a_missing_connector_comes_with_its_switch():
    data = capabilities.inventory(tools("triggers"), FakeSettings(mail=False))
    text = capabilities.render_prompt(data)
    assert "Not connected in this build: mail" in text
    assert "Settings → Connectors → Mail" in text
    assert "MCP server" in text


def test_a_dangerous_tool_is_marked():
    data = capabilities.inventory(
        [FakeTool("send_email", "Send a letter.", "sensitive")], FakeSettings(mail=True)
    )
    line = data["groups"][0]["tools"][0]["line"]
    assert "needs the user's yes" in line


def test_the_markdown_is_a_readable_file():
    data = capabilities.inventory(
        tools("read_emails", "files"), FakeSettings(mail=True, calendar=True, contacts=True)
    )
    text = capabilities.render_markdown(data)
    assert text.startswith("# Что умеет")
    assert "## Mail - почта" in text
    assert "`read_emails`" in text
    assert "## Правила" in text


def test_the_prompt_carries_the_inventory():
    from nanomuse import prompts

    assert "{capabilities}" in prompts.SYSTEM_PROMPT
    assert "{inventory}" in prompts.CAPABILITIES_SECTION


def test_the_committed_file_is_current():
    """``CAPABILITIES.md`` в репозитории должен совпадать с реестром."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "capabilities_md.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_agents_are_pointed_at_the_file():
    for name in ("AGENTS.md", "CLAUDE.md"):
        text = (ROOT / name).read_text(encoding="utf-8") if (ROOT / name).exists() else ""
        if text:
            assert "CAPABILITIES.md" in text, f"{name} does not point at the inventory"


def test_agent_inventory_tracks_live_tool_add_remove_and_settings():
    from types import SimpleNamespace

    from nanomuse.agent.core import MuseAgent
    from nanomuse.tools import ToolCollection

    registry = ToolCollection(*tools("files", "read_emails"))
    agent = SimpleNamespace(tools=registry, settings=FakeSettings(mail=False))

    def render():
        return MuseAgent.capabilities_section(agent)

    assert "`phone_task`" not in render()
    registry.add(*tools("phone_task"))
    assert "`phone_task`" in render()
    registry.remove("phone_task")
    assert "`phone_task`" not in render()
    assert "Not connected in this build: mail" in render()
    agent.settings = FakeSettings(mail=True)
    assert "Not connected in this build: mail" not in render()
