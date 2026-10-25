from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nanomuse.channels.base import InboundMessage
from nanomuse.channels.menu import ChatMenu, Preferences
from nanomuse.channels.permission_mode import scoped_decision
from nanomuse.sentinel.policy import Decision
from nanomuse.tools.base import CallAssessment


def manager(tmp_path):
    svc = SimpleNamespace(
        data_dir=tmp_path,
        app=SimpleNamespace(sentinel=SimpleNamespace()),
        settings=SimpleNamespace(channels=SimpleNamespace(shared_thread=False)),
        hub=SimpleNamespace(primary=lambda: None),
        # the menu binds a ticket to the account and asks it which threads are the person's:
        # a conversation this fake does not know is not the account's, so the chats list
        # stays empty and a ticket still names the account it was made for
        sync=SimpleNamespace(
            account_id="acct-under-test",
            _mine=lambda thread: False,
            owner_of=lambda thread: "",
            cid_of=lambda thread: "",
        ),
    )
    channel = SimpleNamespace(_call=AsyncMock())
    m = SimpleNamespace(
        svc=svc,
        lang="ru",
        channels={"telegram": channel, "max": channel},
        _thread_for=lambda msg: f"channel-{msg.channel}-{msg.chat_id}",
        _allowed=lambda msg: True,
        _send=AsyncMock(),
        pairing=SimpleNamespace(
            is_approved=lambda *args: True, approved=lambda channel: {"owner": {"chat_id": "chat"}}
        ),
        store=SimpleNamespace(config=lambda channel: SimpleNamespace(allow_from=[], settings={})),
    )
    return m


def msg(channel="telegram", chat="chat", sender="owner", text="/menu", is_group=False):
    return InboundMessage(
        channel=channel, chat_id=chat, sender_id=sender, text=text, is_group=is_group
    )


def test_preferences_persist_without_cross_chat_access(tmp_path):
    p = Preferences(tmp_path / "prefs.json")
    p.put("one", mode="full-access", language="en")
    assert Preferences(p.path).mode("one") == "full-access"
    assert p.mode("two") == ""
    assert p.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["telegram", "max"])
async def test_menu_has_all_controls_and_private_callback(channel, tmp_path):
    m = manager(tmp_path)
    menu = ChatMenu(m)
    message = msg(channel)
    assert await menu.inbound(message)
    assert len(menu.tickets) == 0  # navigation survives restarts; changes use tickets
    args = m.channels[channel]._call.call_args
    # the home menu leads to the account console; the price list lives on the pay page
    assert "https://cloud.nanolika.ru/web/account/?source=messenger" in str(args)
    token = "nav:permissions"
    before = m.channels[channel]._call.call_count
    await menu.callback(channel, "other", "owner", {"menu": token})
    await menu.callback(channel, "chat", "intruder", {"menu": token})
    assert m.channels[channel]._call.call_count == before
    await menu.callback(channel, "chat", "owner", {"menu": token})
    assert len(menu.tickets) >= 3
    assert "mode" not in menu.preferences.get("channel-" + channel + "-chat")


@pytest.mark.asyncio
async def test_full_access_requires_separate_confirm_and_not_shared_thread(tmp_path):
    m = manager(tmp_path)
    menu = ChatMenu(m)
    message = msg()
    await menu.perform(message, "mode:full-access")
    assert menu.preferences.mode(m._thread_for(message)) == ""
    assert "Включить" in str(m.channels["telegram"]._call.call_args)
    await menu.perform(message, "mode-confirm:full-access")
    assert menu.preferences.mode(m._thread_for(message)) == "full-access"
    m.svc.settings.channels.shared_thread = True
    await menu.perform(msg(chat="second"), "mode-confirm:full-access")
    assert menu.preferences.mode("channel-telegram-second") == ""


@pytest.mark.asyncio
async def test_group_menu_does_not_disclose_balance_or_preferences(tmp_path):
    m = manager(tmp_path)
    menu = ChatMenu(m)
    assert await menu.inbound(msg(is_group=True))
    assert "личном чате" in str(m._send.call_args)


@pytest.mark.asyncio
async def test_language_is_per_conversation(tmp_path):
    m = manager(tmp_path)
    menu = ChatMenu(m)
    message = msg()
    await menu.perform(message, "lang:en")
    assert menu.language(message) == "en"
    assert menu.language(msg(chat="two")) == "ru"


def test_restricted_modes_cannot_escape_or_run_arbitrary_code(tmp_path):
    tool = SimpleNamespace(name="files", workspace=tmp_path / "work")
    tool.workspace.mkdir()
    a = CallAssessment()
    assert (
        scoped_decision(
            "workspace-write", tool, {"action": "write", "path": "ok.txt"}, a, Decision.ASK
        )
        == Decision.ALLOW
    )
    assert (
        scoped_decision(
            "workspace-write", tool, {"action": "write", "path": "../outside"}, a, Decision.ALLOW
        )
        == Decision.DENY
    )
    (tool.workspace / "link").symlink_to(tmp_path)
    assert (
        scoped_decision(
            "workspace-write", tool, {"action": "write", "path": "link/outside"}, a, Decision.ALLOW
        )
        == Decision.DENY
    )
    assert (
        scoped_decision("read-only", tool, {"action": "write", "path": "ok"}, a, Decision.ALLOW)
        == Decision.DENY
    )
    assert (
        scoped_decision("read-only", tool, {"action": "read", "path": "ok"}, a, Decision.ALLOW)
        == Decision.ALLOW
    )
    assert (
        scoped_decision("read-only", SimpleNamespace(name="shell"), {}, a, Decision.ALLOW)
        == Decision.DENY
    )
    assert (
        scoped_decision("workspace-write", SimpleNamespace(name="shell"), {}, a, Decision.ALLOW)
        == Decision.ASK
    )
    assert scoped_decision("full-access", tool, {}, a, Decision.ASK) == Decision.ALLOW
    assert scoped_decision("full-access", tool, {}, a, Decision.DENY) == Decision.DENY


@pytest.mark.asyncio
async def test_expired_or_unpaired_callback_is_rejected(tmp_path):
    m = manager(tmp_path)
    menu = ChatMenu(m)
    message = msg()
    t = menu.ticket(message, "mode-confirm:full-access")["menu"]
    m._allowed = lambda _: False
    await menu.callback("telegram", "chat", "owner", {"menu": t})
    assert menu.preferences.mode("channel-telegram-chat") == ""


@pytest.mark.asyncio
async def test_scoped_gate_does_not_ask_in_full_mode_or_change_another_thread(tmp_path):
    import json

    from nanomuse.config import SentinelSettings
    from nanomuse.schema import Function, RiskLevel, ToolCall, ToolResult
    from nanomuse.sentinel import AuditLog, Sentinel
    from nanomuse.tools.base import BaseTool
    from nanomuse.ui import HeadlessUI

    class Change(BaseTool):
        name: str = "change"
        risk: RiskLevel = RiskLevel.SENSITIVE
        description: str = "changes state"
        parameters: dict = {"type": "object", "properties": {}}

        async def execute(self, **kwargs):
            return ToolResult(output="changed")

    ui = HeadlessUI(approve=False)
    gate = Sentinel(SentinelSettings(), AuditLog(tmp_path / "audit.jsonl"), ui)
    gate.channel_mode = lambda thread: "full-access" if thread == "own" else ""
    call = ToolCall(function=Function(name="change", arguments=json.dumps({})))
    token = gate.begin_task(conversation="own")
    try:
        assert (await gate.guard(call, Change())).output == "changed"
    finally:
        gate.end_task(token)
    token = gate.begin_task(conversation="other")
    try:
        assert (await gate.guard(call, Change())).error
    finally:
        gate.end_task(token)


@pytest.mark.asyncio
async def test_menu_configuration_matches_vendor_command_shapes(tmp_path):
    m = manager(tmp_path)
    menu = ChatMenu(m)
    for vendor in ("telegram", "max"):
        ch = SimpleNamespace(name=vendor, _call=AsyncMock())
        await menu.configure(ch)
        call = ch._call.call_args_list[0]
        commands = (
            call.args[1]["commands"] if vendor == "telegram" else call.kwargs["json"]["commands"]
        )
        field = "command" if vendor == "telegram" else "name"
        # the vendor command list is the three doors into the menu; permissions, balance,
        # pay and language are inline buttons inside it, so they are not registered here
        assert {row[field] for row in commands} == {"menu", "new", "chats"}


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["telegram", "max"])
async def test_old_menu_and_expired_permission_buttons_never_fail_silently(channel, tmp_path):
    m = manager(tmp_path)
    old = ChatMenu(m)
    message = msg(channel)
    token = old.ticket(message, "mode-confirm:full-access")["menu"]
    fresh = ChatMenu(m)
    await fresh.callback(channel, "chat", "owner", {"menu": "nav:permissions"})
    assert m.channels[channel]._call.called
    await fresh.callback(channel, "chat", "owner", {"menu": token})
    assert "устарела" in str(m.channels[channel]._call.call_args)
    assert fresh.preferences.mode(m._thread_for(message)) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["telegram", "max"])
async def test_permissions_command_really_sends_mode_buttons(channel, tmp_path):
    m = manager(tmp_path)
    menu = ChatMenu(m)
    assert await menu.inbound(msg(channel, text="/permissions"))
    payload = str(m.channels[channel]._call.call_args)
    for label in ("Полный доступ", "Запись в рабочей папке", "Только чтение"):
        assert label in payload
