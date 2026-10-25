"""What the build already has must reach the prompt.

The case these tests exist for: "watch my mail" once sent the agent off to write an IMAP
client and install an MCP server for mail, while the runtime had the mail connector and mail
triggers all along. The tools were there; the sentence saying so was not.
"""

from __future__ import annotations

from pathlib import Path

from nanomuse.config import Settings
from nanomuse.ui import HeadlessUI


def build(tmp_path: Path, *, mail: bool = False, calendar: bool = False):
    from nanomuse.app import NanoMuseApp

    settings = Settings()
    settings.data_dir = tmp_path
    settings.agent.workspace = tmp_path / "ws"
    settings.connectors.email.enabled = mail
    settings.connectors.email.imap_host = "imap.yandex.ru" if mail else ""
    settings.connectors.email.address = "someone@example.com"
    settings.connectors.email.password = "not-a-real-password"
    settings.connectors.calendar.enabled = calendar
    return NanoMuseApp(settings, ui=HeadlessUI(), session_id="test")


def section(app) -> str:
    agent = app.agent
    inner = getattr(agent, "core", agent)
    return inner.connectors_section()


def test_mail_on_is_described_as_on(tmp_path: Path):
    app = build(tmp_path, mail=True)
    text = section(app)
    assert "Mail is connected" in text
    assert "read_emails" in text and "send_email" in text
    # and it points at triggers, which is what "watch my mail" actually needs
    assert "kind=mail" in text
    # and it forbids the two wrong turns explicitly
    assert "IMAP client" in text and "MCP server" in text


def test_mail_off_says_where_the_switch_is(tmp_path: Path):
    app = build(tmp_path, mail=False)
    text = section(app)
    assert "Mail is not connected" in text
    assert "Settings → Connectors → Mail" in text
    assert "IMAP client" in text
    assert "MCP server" in text


def test_calendar_is_described_the_same_way(tmp_path: Path):
    assert "calendar is not connected" in section(build(tmp_path, calendar=False))
    assert "calendar is connected" in section(build(tmp_path, calendar=True))


def test_the_rule_is_in_the_system_prompt():
    from nanomuse import prompts

    assert "Before building a capability" in prompts.SYSTEM_PROMPT
    assert "store" in prompts.SYSTEM_PROMPT


def test_a_mail_trigger_reports_the_connector(tmp_path: Path):
    app = build(tmp_path, mail=True)
    assert app.trigger_kinds()["mail"] is True
    other = build(tmp_path / "b", mail=False)
    assert other.trigger_kinds()["mail"] is False


def test_the_tools_really_are_there(tmp_path: Path):
    app = build(tmp_path, mail=True)
    names = {t.name for t in app.tools}
    assert {"read_emails", "send_email", "triggers"} <= names
    quiet = build(tmp_path / "c", mail=False)
    assert "read_emails" not in {t.name for t in quiet.tools}
