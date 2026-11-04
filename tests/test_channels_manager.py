"""The channel manager end to end, with a fake channel plugged into a real server and a
scripted model: pairing, allowlist, group policy, replies, approvals by word, delivery."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nanomuse.channels.base import Channel, ChannelStatus, Field, InboundFile, InboundMessage
from nanomuse.channels.manager import ChannelManager, thread_id_for
from nanomuse.config import Settings
from nanomuse.hub.client import HubError
from nanomuse.llm import MockLLM
from nanomuse.schema import Function, LLMResponse, ToolCall
from nanomuse.server import create_app
from nanomuse.server.service import MuseService


def tc(name: str, **args: Any) -> ToolCall:
    return ToolCall(function=Function(name=name, arguments=json.dumps(args)))


def wait_for(pred, timeout: float = 8.0, interval: float = 0.03):  # noqa: ANN001
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = pred()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met in time")


class FakeChannel(Channel):
    name = "fake"
    label = "Fake"
    supports_edit = True
    console_url = "https://example.invalid/console"
    fields = (Field("token", "Token", "secret", required=True),)
    instances: list[FakeChannel] = []

    def __init__(self, settings: dict[str, Any]):
        super().__init__(settings)
        self.sent: list[tuple[str, str, list[str]]] = []
        self.streams: list[tuple[str, str, str, bool]] = []
        self.asks: list[tuple[str, str, str]] = []
        self.ask_langs: list[str] = []
        self.typing_calls: list[tuple[str, bool]] = []
        self.stopped = False
        FakeChannel.instances.append(self)

    async def start(self, bus) -> None:  # noqa: ANN001
        await super().start(bus)
        bus.set_status(self.name, "connected", "fake bot")

    async def stop(self) -> None:
        self.stopped = True

    async def send(self, chat_id: str, text: str, *, files: list[str] | None = None) -> str:
        self.sent.append((chat_id, text, list(files or [])))
        return "m1"

    async def stream(self, chat_id: str, stream_id: str, text: str, *, done: bool) -> None:
        self.streams.append((chat_id, stream_id, text, done))

    async def ask(self, chat_id: str, approval_id: str, text: str, lang: str = "") -> None:
        self.asks.append((chat_id, approval_id, text))
        self.ask_langs.append(lang)

    async def typing(self, chat_id: str, on: bool) -> None:
        self.typing_calls.append((chat_id, on))

    async def verify(self) -> str:
        return "Bot: fake"

    # ---- what the test says
    def texts(self, chat_id: str = "chat1") -> list[str]:
        """Everything the chat saw: plain sends and completed streams, in order."""
        out = [t for c, t, _ in self.sent if c == chat_id]
        out += [t for c, _, t, done in self.streams if c == chat_id and done]
        return out


def emit(manager: ChannelManager, message: dict[str, Any]) -> None:
    """Publish on the service bus from the test thread (asyncio queues are not thread-safe)."""
    assert manager._loop is not None
    manager._loop.call_soon_threadsafe(manager.svc.bus.publish, message)


def dm(text: str, sender: str = "u1", chat: str = "chat1", **kw: Any) -> InboundMessage:
    return InboundMessage(
        channel="fake", chat_id=chat, sender_id=sender, sender_name="Ann", text=text, **kw
    )


@pytest.fixture()
def chan(
    settings: Settings,
) -> Iterator[tuple[TestClient, MuseService, MockLLM, ChannelManager, FakeChannel]]:
    settings.server.token = "secret-token"
    settings.sentinel.always_allow_tools = ["python_execute"]
    settings.agent.language = "en"
    llm = MockLLM([])
    service = MuseService(settings, llm=llm)
    app = create_app(settings, service)
    manager: ChannelManager = service.channels  # type: ignore[attr-defined]
    manager.types["fake"] = FakeChannel
    manager.status["fake"] = ChannelStatus()
    manager.store.update(FakeChannel, enabled=True, values={"token": "t0k"})
    FakeChannel.instances.clear()
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer secret-token"
        wait_for(lambda: manager.status["fake"].state == "connected")
        yield client, service, llm, manager, FakeChannel.instances[0]


def pair(client: TestClient, fake: FakeChannel, sender: str = "u1", chat: str = "chat1") -> str:
    fake.bus.inbound(dm("hello", sender=sender, chat=chat))
    pending = wait_for(lambda: client.get("/api/channels").json()["pending"])
    code = next(p["code"] for p in pending if p["sender_id"] == sender)
    n = len(fake.sent)
    assert client.post(f"/api/channels/pairing/{code}/approve").status_code == 200
    wait_for(lambda: len(fake.sent) > n)
    return code


def test_channel_send_redacts_vault_values(chan):
    _, service, _, manager, fake = chan
    secret = "ghp_SuperSecretToken123"
    service.app.vault.set("GITHUB_TOKEN", secret)
    asyncio.run_coroutine_threadsafe(
        manager._send("fake", "chat1", f"Here is {secret}."), manager._loop
    ).result(timeout=5)
    assert fake.sent[-1][1] == "Here is [REDACTED:GITHUB_TOKEN]."


def test_thread_ids_are_file_safe():
    assert thread_id_for("feishu", "oc_abc:123") == "channel-feishu-oc_abc_123"
    assert thread_id_for("dingtalk", "group:cid+x/y") == "channel-dingtalk-group_cid_x_y"
    assert thread_id_for("telegram", "-100123") == "channel-telegram--100123"
    assert thread_id_for("wecom", "") == "channel-wecom-chat"


def test_first_message_gets_a_pairing_code_and_approval_pairs(chan):
    client, service, llm, manager, fake = chan
    view = client.get("/api/channels").json()
    me = next(c for c in view["channels"] if c["name"] == "fake")
    assert me["status"] == {"state": "connected", "detail": "fake bot"} and me["enabled"]
    assert me["fields"][0]["has_value"] is True and "value" not in me["fields"][0]

    fake.bus.inbound(dm("hello there"))
    wait_for(lambda: fake.sent)
    chat, text, _ = fake.sent[0]
    pending = client.get("/api/channels").json()["pending"]
    assert len(pending) == 1 and pending[0]["sender_name"] == "Ann"
    code = pending[0]["code"]
    assert (
        chat == "chat1" and code in text and "nanoLika" in text and "nanomuse" not in text.lower()
    )
    # nothing reached the agent
    assert "channel-fake-chat1" not in service.threads and llm.calls == []
    # the same code again while it is good; a bad code is a 404
    fake.bus.inbound(dm("still me"))
    wait_for(lambda: len(fake.sent) >= 2)
    assert code in fake.sent[1][1]
    assert client.post("/api/channels/pairing/ZZZZZZ/approve").status_code == 404

    r = client.post(f"/api/channels/pairing/{code}/approve")
    assert r.status_code == 200 and r.json()["paired"]["sender_id"] == "u1"
    wait_for(lambda: len(fake.sent) >= 3)
    assert "I am Lika" in fake.sent[2][1]
    me = next(c for c in r.json()["channels"] if c["name"] == "fake")
    assert (
        me["paired"][0]["sender_id"] == "u1" and me["paired"][0]["thread"] == "channel-fake-chat1"
    )
    assert client.get("/api/channels").json()["pending"] == []

    # now the message goes to the agent and the reply comes back
    llm.script.append(LLMResponse(content="Hi Ann, glad you are here."))
    fake.bus.inbound(dm("hello again"))
    wait_for(lambda: any("Hi Ann" in t for t in fake.texts()))
    thread = service.threads["channel-fake-chat1"]
    assert thread.title == "Fake · Ann"
    assert llm.calls and "hello again" in str(llm.calls[-1]["messages"][-1])


def test_deny_and_allowlist(chan):
    client, service, llm, manager, fake = chan
    fake.bus.inbound(dm("hi", sender="u9"))
    code = wait_for(lambda: client.get("/api/channels").json()["pending"])[0]["code"]
    assert client.post(f"/api/channels/pairing/{code}/deny").status_code == 200
    assert client.get("/api/channels").json()["pending"] == []
    assert client.post(f"/api/channels/pairing/{code}/deny").status_code == 404
    # someone on the allowlist needs no code
    r = client.put("/api/channels/fake", json={"allow_from": ["boss"]})
    assert r.status_code == 200
    llm.script.append(LLMResponse(content="Yes boss."))
    fake.bus.inbound(dm("status?", sender="boss", chat="dm-boss"))
    wait_for(lambda: any("Yes boss" in t for t in fake.texts("dm-boss")))
    assert client.get("/api/channels").json()["pending"] == []


def test_group_policy_mention_or_open(chan):
    client, service, llm, manager, fake = chan
    pair(client, fake)
    # a group message without an @ is ignored under "mention"
    fake.bus.inbound(dm("chatter", chat="g1", is_group=True, mentioned=False, chat_name="Team"))
    time.sleep(0.3)
    assert "channel-fake-g1" not in service.threads
    llm.script.append(LLMResponse(content="Here is the summary."))
    fake.bus.inbound(
        dm("@bot summarize", chat="g1", is_group=True, mentioned=True, chat_name="Team")
    )
    wait_for(lambda: any("summary" in t for t in fake.texts("g1")))
    assert service.threads["channel-fake-g1"].title == "Fake · Team"
    # the sender's name travels with a group message
    assert "Ann:" in str(llm.calls[-1]["messages"][-1])
    # under "open" every group message counts
    assert client.put("/api/channels/fake", json={"group_policy": "open"}).status_code == 200
    llm.script.append(LLMResponse(content="Noted."))
    fake.bus.inbound(dm("fyi", chat="g1", is_group=True, mentioned=False))
    wait_for(lambda: any("Noted" in t for t in fake.texts("g1")))
    # unknown people in a group are ignored quietly, no code
    n = len(fake.sent)
    fake.bus.inbound(dm("hey", sender="stranger", chat="g1", is_group=True))
    time.sleep(0.3)
    assert len(fake.sent) == n and client.get("/api/channels").json()["pending"] == []


def test_approval_is_asked_in_the_chat_and_answered_with_a_word(chan):
    client, service, llm, manager, fake = chan
    pair(client, fake)
    llm.script.extend(
        [
            LLMResponse(content="Running it.", tool_calls=[tc("shell", command="echo from-chat")]),
            LLMResponse(content="Done, it printed from-chat."),
        ]
    )
    fake.bus.inbound(dm("run echo"))
    chat, approval_id, text = wait_for(lambda: fake.asks)[0]
    assert chat == "chat1" and "needs your OK" in text and "allow" in text
    # a tool running shows as "working" in the chat
    wait_for(lambda: ("chat1", True) in fake.typing_calls)
    fake.bus.inbound(dm("allow"))
    wait_for(lambda: any("Allowed." in t for t in fake.texts()))
    wait_for(lambda: any("from-chat" in t for t in fake.texts()))
    wait_for(lambda: ("chat1", False) in fake.typing_calls)
    # the card can only be answered once
    fake.bus.inbound(dm("deny"))
    time.sleep(0.3)
    assert not any("no longer waiting" in t for t in fake.texts())  # a plain word is a message now


def test_button_press_decides_too(chan):
    client, service, llm, manager, fake = chan
    pair(client, fake)
    llm.script.extend(
        [
            LLMResponse(tool_calls=[tc("shell", command="echo nope")]),
            LLMResponse(content="Understood."),
        ]
    )
    fake.bus.inbound(dm("run something"))
    _, approval_id, _ = wait_for(lambda: fake.asks)[0]
    # a stranger's press is ignored
    fake.bus.action("fake", "chat1", "stranger", {"approval": approval_id, "decision": "deny"})
    time.sleep(0.2)
    fake.bus.action("fake", "chat1", "u1", {"approval": approval_id, "decision": "deny"})
    wait_for(lambda: any("Denied." in t for t in fake.texts()))
    wait_for(lambda: any("Understood" in t for t in fake.texts()))


def test_deliver_here_gets_background_events(chan):
    client, service, llm, manager, fake = chan
    pair(client, fake)
    assert client.post("/api/channels/deliver", json={"text": "ping"}).json() == {"sent": 0}
    r = client.put("/api/channels/fake/chats/u1", json={"deliver": True})
    assert r.status_code == 200
    me = next(c for c in r.json()["channels"] if c["name"] == "fake")
    assert me["paired"][0]["deliver"] is True
    assert client.post("/api/channels/deliver", json={"text": "ping"}).json() == {"sent": 1}
    wait_for(lambda: ("chat1", "ping", []) in fake.sent)
    # a background run's final words reach the chat; quiet ones and non-final ones do not
    emit(
        manager,
        {
            "kind": "event",
            "event": {
                "type": "assistant",
                "thread": "side-task",
                "text": "The report is ready.",
                "final": True,
                "source": "background",
                "about": "Weekly report",
            },
        },
    )
    wait_for(lambda: any("Weekly report" in t and "report is ready" in t for t in fake.texts()))
    emit(
        manager,
        {
            "kind": "event",
            "event": {
                "type": "assistant",
                "thread": "side-task",
                "text": "shh",
                "final": True,
                "quiet": True,
            },
        },
    )
    # a pending approval elsewhere is asked here, and a word here decides it
    emit(
        manager,
        {
            "kind": "event",
            "event": {
                "type": "approval",
                "thread": "side-task",
                "id": "ap-1",
                "status": "pending",
                "summary": "send an e-mail",
                "purpose": "weekly report",
            },
        },
    )
    wait_for(lambda: any(a[1] == "ap-1" for a in fake.asks))
    fake.bus.inbound(dm("拒绝"))
    wait_for(lambda: any("no longer waiting" in t for t in fake.texts()))
    assert not any("shh" in t for t in fake.texts())
    # a question from elsewhere is asked here and the next line answers it
    emit(
        manager,
        {
            "kind": "event",
            "event": {
                "type": "question",
                "thread": "side-task",
                "text": "Which week?",
                "status": "pending",
            },
        },
    )
    wait_for(lambda: any("Which week?" in t for t in fake.texts()))
    # switch delivery off again
    assert client.put("/api/channels/fake/chats/u1", json={"deliver": False}).status_code == 200
    assert client.post("/api/channels/deliver", json={"text": "ping2"}).json() == {"sent": 0}
    assert client.put("/api/channels/fake/chats/nobody", json={"deliver": True}).status_code == 404


def test_unrelated_synced_turns_are_not_broadcast(chan):
    client, _service, _llm, manager, fake = chan
    pair(client, fake)
    fake.sent.clear()
    assert client.put("/api/channels/fake/chats/u1", json={"deliver": True}).status_code == 200
    import asyncio

    future = asyncio.run_coroutine_threadsafe(
        manager._route_event(
            {
                "type": "user",
                "thread": "main",
                "text": "private unrelated chat",
                "synced": True,
                "mid": "fixture-unrelated",
                "via_device": "fixture-pc",
                "ts": "2099-01-01T00:00:00+00:00",
            }
        ),
        manager._loop,
    )
    future.result(timeout=5)
    assert not fake.sent


def test_one_memory_shared_chats_write_into_the_main_thread(chan):
    """`channels.shared_thread`: a chat's message lands in the account's main conversation
    (one history for the bot, the phone and the computer), and the answer still comes back
    into that chat."""
    from nanomuse.server.events import MAIN_THREAD

    client, service, llm, manager, fake = chan
    service.settings.channels.shared_thread = True
    pair(client, fake)
    llm.script.append(LLMResponse(content="Here is what we said on the computer."))
    fake.bus.inbound(dm("what did we say?"))
    wait_for(lambda: any("Here is what we said" in t for t in fake.texts()))
    assert MAIN_THREAD in manager.routes
    assert thread_id_for("fake", "chat1") not in manager.routes


def test_an_online_computer_does_not_take_an_ordinary_chat_message(chan, monkeypatch):
    """The computer is hands, not the brain. With its app online and `hub.primary` naming it,
    an ordinary message is still answered here: sending every turn to the desktop made a
    chat wait on a machine, ask its cards and end with "the computer has not finished"."""
    client, service, llm, manager, fake = chan
    pair(client, fake)
    calls: list[str] = []

    async def call(
        to: str,
        action: str,
        args: dict[str, Any] | None = None,
        timeout: float = 120,
        on_event=None,
    ):  # noqa: ANN001
        calls.append(action)
        return {"text": "The shared chat answered."}

    llm.script.append(LLMResponse(content="Answered here."))
    monkeypatch.setattr(
        service.hub, "primary", lambda: {"id": "dev-1", "name": "MacBook", "online": True}
    )
    monkeypatch.setattr(service.hub, "call", call)
    fake.bus.inbound(dm("are you there?"))
    wait_for(lambda: any("Answered here." in t for t in fake.texts()))
    assert calls == [], "an ordinary message is never handed to the desktop"


def test_computer_command_hands_the_turn_to_the_desktop(chan, monkeypatch):
    """`/computer` is the one explicit way to use the hands: the words travel to the
    computer as a task and its answer lands in the chat."""
    client, service, llm, manager, fake = chan
    pair(client, fake)
    calls: list[tuple[str, str, dict[str, Any]]] = []

    async def call(
        to: str,
        action: str,
        args: dict[str, Any] | None = None,
        timeout: float = 120,
        on_event=None,
    ):  # noqa: ANN001
        calls.append((to, action, dict(args or {})))
        return {"text": "Готово на компьютере."}

    def others():
        return [
            {
                "id": "dev-1",
                "name": "MacBook",
                "kind": "computer",
                "online": True,
                "actions": ["task"],
            }
        ]

    monkeypatch.setattr(service.hub, "others", others)
    monkeypatch.setattr(service.hub, "call", call)
    fake.bus.inbound(dm("/computer сделай файл"))
    wait_for(lambda: any("Готово на компьютере" in t for t in fake.texts()))
    assert calls and calls[0][0] == "dev-1" and calls[0][1] == "task"
    assert "сделай файл" in calls[0][2]["text"]


def test_files_in_and_out(chan, settings: Settings):
    client, service, llm, manager, fake = chan
    pair(client, fake)
    llm.script.append(LLMResponse(content="Got your picture."))
    fake.bus.inbound(
        InboundMessage(
            channel="fake",
            chat_id="chat1",
            sender_id="u1",
            text="",
            files=[InboundFile(name="shot.png", data=b"\x89PNG fake", kind="image")],
        )
    )
    wait_for(lambda: any("Got your picture" in t for t in fake.texts()))
    saved = list((settings.data_dir / "workspaces").rglob("*.png"))
    assert saved and saved[0].read_bytes() == b"\x89PNG fake"
    # an artifact the run produced goes out with the reply
    (settings.agent.workspace / "out.txt").write_text("hello", "utf-8")
    emit(
        manager,
        {
            "kind": "event",
            "event": {"type": "artifact", "thread": "channel-fake-chat1", "path": "out.txt"},
        },
    )
    emit(manager, {"kind": "status", "status": {"thread": "channel-fake-chat1", "state": "idle"}})
    wait_for(lambda: any(files for _, _, files in fake.sent))
    files = next(files for _, _, files in fake.sent if files)
    assert files[0].endswith("out.txt")


def test_a_picture_from_the_conversation_workspace_is_sent(chan):
    """A file the turn made lives in the conversation's own workspace
    (`…/threads/<id>/pictures/…`), while the artifact event names it relative to that
    workspace. Resolved against the installation workspace it was not found, and the
    channels skip a missing file without a word: the picture never reached the chat."""
    client, service, llm, manager, fake = chan
    pair(client, fake)
    own = thread_id_for("fake", "chat1")
    llm.script.append(LLMResponse(content="Here is the fox."))
    fake.bus.inbound(dm("draw a fox"))
    wait_for(lambda: any("Here is the fox" in t for t in fake.texts()))

    pictures = service.workspace_for_thread(own) / "pictures"
    pictures.mkdir(parents=True, exist_ok=True)
    (pictures / "fox.png").write_bytes(b"\x89PNG fox")
    emit(
        manager,
        {
            "kind": "event",
            "event": {"type": "artifact", "thread": own, "path": "pictures/fox.png"},
        },
    )
    emit(manager, {"kind": "status", "status": {"thread": own, "state": "idle"}})
    wait_for(lambda: any(files for _, _, files in fake.sent))
    sent = next(files for _, _, files in fake.sent if files)
    assert sent[0].endswith("pictures/fox.png")
    assert (pictures / "fox.png").is_file()


def test_a_file_made_on_the_computer_arrives_as_a_document(chan, monkeypatch):
    """The answer of a desktop turn names the file by a path of that computer
    (`/Users/…`), which a phone cannot open. The bytes are fetched over the hub and the
    chat gets the file itself; the words keep only its name."""
    client, service, llm, manager, fake = chan
    pair(client, fake)
    calls: list[tuple[str, str, dict[str, Any]]] = []
    payload = base64.b64encode(b"xlsx-bytes").decode()

    async def call(
        to: str,
        action: str,
        args: dict[str, Any] | None = None,
        timeout: float = 120,
        on_event=None,
    ):  # noqa: ANN001
        calls.append((to, action, dict(args or {})))
        if action == "task":
            return {"text": "Готово.\n\n[Скачать book.xlsx](</Users/ann/book.xlsx>)"}
        if action == "file.get":
            return {"name": "book.xlsx", "data": payload}
        return {}

    def others():
        return [
            {
                "id": "dev-1",
                "name": "MacBook",
                "kind": "computer",
                "online": True,
                "actions": ["task"],
            }
        ]

    monkeypatch.setattr(service.hub, "others", others)
    monkeypatch.setattr(service.hub, "call", call)
    fake.bus.inbound(dm("/computer сделай таблицу excel"))
    wait_for(lambda: any(files for _, _, files in fake.sent))
    _chat, text, files = next(item for item in fake.sent if item[2])
    assert files[0].endswith("book.xlsx")
    assert Path(files[0]).read_bytes() == b"xlsx-bytes"
    assert "/Users/ann/book.xlsx" not in text and "Скачать book.xlsx" in text
    assert ("dev-1", "file.get", {"path": "/Users/ann/book.xlsx"}) in calls


def test_switching_off_and_on_and_removing_a_chat(chan):
    client, service, llm, manager, fake = chan
    pair(client, fake)
    r = client.put("/api/channels/fake", json={"enabled": False})
    assert r.status_code == 200
    me = next(c for c in r.json()["channels"] if c["name"] == "fake")
    assert me["status"]["state"] == "off" and fake.stopped
    assert client.post("/api/channels/fake/test").status_code == 409
    r = client.put("/api/channels/fake", json={"enabled": True})
    me = next(c for c in r.json()["channels"] if c["name"] == "fake")
    assert me["status"]["state"] == "connected" and len(FakeChannel.instances) == 2
    fresh = FakeChannel.instances[-1]
    # a test message goes to the first paired chat; without one the credentials are checked
    r = client.post("/api/channels/fake/test")
    assert r.status_code == 200 and r.json()["detail"] == "Sent to chat1."
    assert fresh.sent and "Test message" in fresh.sent[-1][1]
    assert client.delete("/api/channels/fake/chats/u1").status_code == 200
    assert client.delete("/api/channels/fake/chats/u1").status_code == 404
    assert client.post("/api/channels/fake/test").json()["detail"] == "Bot: fake"
    # unpaired again: a message is met with a code
    fresh.bus.inbound(dm("hello?"))
    wait_for(lambda: any("Pairing code" in t for t in fresh.texts()))


def test_reload_picks_up_cli_edits(chan):
    client, service, llm, manager, fake = chan
    # as `nanomuse channels approve` does when the server runs elsewhere: edit the file
    fake.bus.inbound(dm("hi", sender="u5", chat="c5"))
    code = wait_for(lambda: client.get("/api/channels").json()["pending"])[0]["code"]
    assert manager.pairing.approve(code)
    assert client.post("/api/channels/reload").status_code == 200
    me = next(c for c in client.get("/api/channels").json()["channels"] if c["name"] == "fake")
    assert [p["sender_id"] for p in me["paired"]] == ["u5"]


def test_a_russian_runtime_gets_russian_system_lines(chan):
    _client, service, _llm, manager, fake = chan
    service.settings.agent.language = "ru"
    pairing = manager.pairing_text("ABC234")
    assert "Код для привязки: ABC234" in pairing
    assert "配对码" not in pairing and "Pairing code" not in pairing
    # a line with no Russian wording yet falls back to English, never to Chinese
    assert manager.say("Passed on.", "已转达。") == "Passed on."
    assert manager.say("Passed on.", "已转达。", "Передала.") == "Передала."


def test_start_in_a_paired_chat_greets_without_a_run(chan):
    client, _service, _llm, _manager, fake = chan
    pair(client, fake)
    fake.sent.clear()
    fake.bus.inbound(dm("/start", sender="u1", chat="chat1"))
    wait_for(lambda: fake.sent)
    text = fake.sent[-1][1]
    assert "I am Lika" in text  # the agent never saw this greeting


def test_a_russian_run_asks_for_approval_in_russian(chan):
    _client, service, _llm, manager, fake = chan
    service.settings.agent.language = "ru"
    assert manager.lang == "ru"
    text = manager.approval_text({"tool": "shell", "summary": "shell: ls"})
    assert "просит разрешение" in text and "允许" not in text
    assert manager._tool_label({"tool": "shell"}) == "Выполняю команду"


def test_the_first_chat_can_claim_a_bot_nobody_owns(chan):
    client, _service, _llm, manager, fake = chan
    manager.store.update(FakeChannel, enabled=True, values={"token": "t0k"})
    cfg = manager.store.config("fake")
    assert not cfg.first_chat_claims  # off unless asked for
    # turn it on the way config.toml does
    data = dict(manager.store._file.load())
    data["fake"] = {**data.get("fake", {}), "first_chat_claims": True}
    manager.store._file.save(data)
    fake.sent.clear()
    fake.bus.inbound(dm("hello", sender="owner", chat="chatA"))
    wait_for(lambda: fake.sent)
    assert manager.pairing.is_approved("fake", "owner")
    # a second stranger still gets a code, not the bot
    n = len(fake.sent)
    fake.bus.inbound(dm("hello", sender="stranger", chat="chatB"))
    wait_for(lambda: len(fake.sent) > n)
    assert not manager.pairing.is_approved("fake", "stranger")


def test_the_greeting_offers_what_the_channel_was_given(chan):
    _client, _service, _llm, manager, fake = chan
    data = dict(manager.store._file.load())
    entry = dict(data.get("fake", {}))
    # the app keeps its fields under "settings", which then wins over the flat table
    entry["settings"] = {
        **entry.get("settings", {}),
        "site_url": "https://nanolika.ru",
        "invite_url": "https://nanolika.ru/?invite=",
    }
    data["fake"] = entry
    manager.store._file.save(data)
    buttons = manager.welcome_links("fake")
    assert [label for label, _ in buttons] == ["Website", "Invite a friend"]
    # nothing is offered that was not set
    assert manager.welcome_links("feishu") == []


def test_plain_greeting_does_not_run_tools(chan):
    client, _service, llm, _manager, fake = chan
    pair(client, fake)
    fake.sent.clear()
    before = len(llm.calls)
    fake.bus.inbound(dm("Привет", sender="u1", chat="chat1"))
    wait_for(lambda: fake.sent)
    assert "Lika" in fake.sent[-1][1]
    assert len(llm.calls) == before


def test_artwork_failure_still_sends_welcome_text(chan, monkeypatch):
    client, _service, llm, _manager, fake = chan
    pair(client, fake)
    fake.sent.clear()

    async def fail(*args):
        raise RuntimeError("fixture failure")

    monkeypatch.setattr("nanomuse.channels.manager.send_welcome_art", fail)
    before = len(llm.calls)
    fake.bus.inbound(dm("/start", sender="u1", chat="chat1"))
    wait_for(lambda: fake.sent)
    assert "I am Lika" in fake.sent[-1][1]
    assert len(llm.calls) == before


def test_explicit_computer_command_uses_dedicated_handoff(chan, monkeypatch):
    client, service, llm, manager, fake = chan
    pair(client, fake)
    fake.sent.clear()
    before = len(llm.calls)
    received = []
    device = {"id": "fixture-pc", "online": True, "kind": "computer", "actions": ["task"]}
    monkeypatch.setattr(service.hub, "others", lambda: [device])

    async def handoff(thread, msg, text, target, *, fallback=True, shared_cid=""):
        received.append((thread, text, target, fallback))

    monkeypatch.setattr(manager, "_serve_from_device", handoff)
    fake.bus.inbound(dm("/computer Reply only: connected"))
    wait_for(lambda: received)
    assert received[0][1] == "Reply only: connected"
    assert received[0][2] == device
    assert received[0][3] is False
    assert len(llm.calls) == before


def test_computer_command_without_online_device_never_runs_on_server(chan, monkeypatch):
    client, service, llm, _manager, fake = chan
    pair(client, fake)
    fake.sent.clear()
    before = len(llm.calls)
    monkeypatch.setattr(service.hub, "others", lambda: [])
    fake.bus.inbound(dm("/computer Edit my file"))
    wait_for(lambda: fake.sent)
    assert "available computer" in fake.sent[-1][1]
    assert len(llm.calls) == before


def test_pairing_registers_channel_session_before_first_task(chan):
    client, service, llm, _manager, fake = chan
    pair(client, fake)
    assert "channel-fake-chat1" in service.threads
    assert llm.calls == []
    thread_count = len(service.threads)
    fake.bus.inbound(dm("/start"))
    wait_for(lambda: len(fake.sent) >= 2)
    assert len(service.threads) == thread_count
    assert llm.calls == []


def test_bot_conversation_titles_are_stable_and_keep_user_renames(chan):
    _client, service, llm, manager, _fake = chan
    for channel, label in (("telegram", "Telegram"), ("max", "MAX")):
        msg = InboundMessage(
            channel=channel, chat_id="fixture", sender_id="fixture-user", sender_name="Fixture name"
        )
        tid = manager._thread_for(msg)
        assert service.threads[tid].title == label + " · nanoLika"
        service.rename_thread(tid, "My own title")
        assert manager._thread_for(msg) == tid
        assert service.threads[tid].title == "My own title"
    assert llm.calls == []


def test_linked_desktop_messages_go_only_to_their_own_bot(chan):
    import asyncio

    from nanomuse.channels.sync_delivery import SyncDeliveryReceipts

    _client, service, _llm, manager, _fake = chan
    targets = {}
    for name in ("telegram", "max"):
        cls = type("Linked" + name, (FakeChannel,), {"name": name})
        ch = cls({"token": "fixture-token"})
        manager.types[name] = cls
        manager.channels[name] = ch
        manager.store.update(cls, enabled=True, values={"token": "fixture-token"})
        manager.pairing.add(name, "fixture-user", chat_id=name + "-chat")
        targets[name] = ch
    manager.routes.clear()  # routing must survive a restart without a new inbound bot message

    def route(event):
        asyncio.run_coroutine_threadsafe(manager._route_event(event), manager._loop).result(
            timeout=5
        )

    def event(channel, role, mid):
        return {
            "type": role,
            "thread": thread_id_for(channel, channel + "-chat"),
            "text": mid,
            "synced": True,
            "mid": mid,
            "via_device": "fixture-pc",
            "ts": "2099-01-01T00:00:00+00:00",
        }

    user = event("telegram", "user", "telegram-user")
    route(user)
    assert targets["telegram"].sent == [("telegram-chat", "💻 You\ntelegram-user", [])]
    assert not targets["max"].sent
    route(user)
    manager.sync_receipts = SyncDeliveryReceipts(manager.sync_receipts.path)
    route(user)
    assert len(targets["telegram"].sent) == 1
    answer = event("telegram", "assistant", "telegram-answer")
    route(answer)
    assert targets["telegram"].sent[-1][1] == "telegram-answer"
    assert not targets["max"].sent
    route(event("max", "assistant", "max-answer"))
    assert targets["max"].sent == [("max-chat", "max-answer", [])]
    ignored = event("telegram", "user", "own-echo")
    ignored["via_device"] = service.hub.device_id
    route(ignored)
    assert len(targets["telegram"].sent) == 2
    ignored = event("telegram", "assistant", "old-plan")
    ignored["ts"] = "2000-01-01T00:00:00+00:00"
    route(ignored)
    assert len(targets["telegram"].sent) == 2
    manager.pairing.remove("telegram", "fixture-user")
    route(event("telegram", "assistant", "after-unpair"))
    assert len(targets["telegram"].sent) == 2


def test_short_bot_greeting_is_synced_once_without_model(chan):
    import asyncio

    _client, service, llm, manager, _fake = chan
    cls = type("LinkedTelegram", (FakeChannel,), {"name": "telegram"})
    ch = cls({"token": "fixture-token"})
    manager.types["telegram"] = cls
    manager.channels["telegram"] = ch
    manager.store.update(cls, enabled=True, values={"token": "fixture-token"})
    manager.pairing.add("telegram", "fixture-user", chat_id="fixture-chat")
    msg = InboundMessage(
        channel="telegram", sender_id="fixture-user", chat_id="fixture-chat", text="Привет"
    )

    async def run():
        await manager._handle_inbound(msg)
        await asyncio.sleep(0.05)

    asyncio.run_coroutine_threadsafe(run(), manager._loop).result(timeout=5)
    tid = thread_id_for("telegram", "fixture-chat")
    events = [e for e in service.timeline(tid).events if e.get("channel_static")]
    assert [e["type"] for e in events] == ["user", "assistant"]
    assert events[1]["final"] is True and not events[1].get("quiet")
    assert len(ch.sent) == 1
    assert not llm.calls
    assert service.threads[tid].agent.messages[-1].role == "assistant"


def test_plain_model_reply_has_typing_without_tool_calls(chan):
    client, service, llm, manager, fake = chan
    pair(client, fake)
    fake.typing_calls.clear()
    llm.script.append(LLMResponse(content="A plain answer without tools."))
    fake.bus.inbound(dm("Please answer a simple question"))
    wait_for(lambda: any("A plain answer without tools." in t for t in fake.texts()))
    wait_for(lambda: ("chat1", False) in fake.typing_calls)
    assert ("chat1", True) in fake.typing_calls
    assert fake.typing_calls.index(("chat1", True)) < fake.typing_calls.index(("chat1", False))


def test_linked_chat_works_with_a_runtime_before_per_chat_cloud_keys(chan):
    client, service, llm, manager, fake = chan
    pair(client, fake)
    llm.script.append(LLMResponse(content="The linked chat still works."))
    current_send = service.send

    def legacy_send(
        thread_id: str,
        text: str,
        source: str = "user",
        label: str = "",
        files: list[str] | None = None,
        language: str = "",
        via: str = "",
    ) -> dict[str, Any]:
        return current_send(
            thread_id,
            text,
            source=source,
            label=label,
            files=files,
            language=language,
            via=via,
        )

    service.send = legacy_send  # type: ignore[method-assign]
    fake.bus.inbound(dm("please answer"))

    wait_for(lambda: any("The linked chat still works." in t for t in fake.texts()))


def test_scoped_approval_is_bound_to_card_chat_and_offered_options(chan):
    import asyncio

    client, service, llm, manager, fake = chan
    pair(client, fake)
    decisions = []
    service.decide = lambda *args: decisions.append(args) or True

    def run(coro):
        return asyncio.run_coroutine_threadsafe(coro, manager._loop).result(timeout=5)

    run(manager._ask("fake", "chat1", "scope-fixture", "Approve", ["once", "conversation"]))
    run(manager._decide("fake", "other-chat", "scope-fixture", True, "conversation"))
    run(manager._decide("fake", "chat1", "scope-fixture", True, "always"))
    assert decisions == []
    run(manager._decide("fake", "chat1", "scope-fixture", True, "conversation"))
    assert len(decisions) == 1
    assert decisions[0][2] == "conversation"
    run(manager._decide("fake", "chat1", "scope-fixture", True, "conversation"))
    assert len(decisions) == 1


def test_details_does_not_approve_and_is_bound_to_chat(chan):
    import asyncio

    client, service, llm, manager, fake = chan
    pair(client, fake)
    decisions = []
    service.decide = lambda *args: decisions.append(args) or True

    def run(coro):
        return asyncio.run_coroutine_threadsafe(coro, manager._loop).result(timeout=5)

    run(manager._ask("fake", "chat1", "detail-fixture", "Approve", ["once"]))
    manager._approval_details["detail-fixture"] = "Fixture technical details"
    before = len(fake.sent)
    run(
        manager._handle_action(
            "fake", "other-chat", "u1", {"approval": "detail-fixture", "decision": "details"}
        )
    )
    assert len(fake.sent) == before
    run(
        manager._handle_action(
            "fake", "chat1", "u1", {"approval": "detail-fixture", "decision": "details"}
        )
    )
    assert any("Fixture technical details" in text for text in fake.texts())
    assert decisions == []
    assert "detail-fixture" in manager._asks["fake:chat1"]


@pytest.mark.asyncio
async def test_another_device_gets_an_echo_not_a_plain_line(tmp_path):
    """M5: a channel that can draw the echo does; the plain line stays the fallback."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from nanomuse.channels.base import InboundMessage
    from nanomuse.channels.manager import ChannelManager

    manager = ChannelManager.__new__(ChannelManager)
    manager.picker = SimpleNamespace(
        targets=lambda thread: [("fake", "other")],
        title_first_request=lambda *a: None,
    )
    manager.types = {"fake": SimpleNamespace(label="Fake")}
    manager.say = lambda en, zh, ru: ru
    sent: list[tuple] = []

    async def send(*args):
        sent.append(args)

    manager._send = send
    echo = AsyncMock()
    manager.channels = {"fake": SimpleNamespace(echo=echo)}
    message = InboundMessage(channel="fake", chat_id="here", sender_id="u1", text="привет")

    await manager._echo_selected_user("main", message, "привет")

    echo.assert_awaited_once_with("other", "Fake", "привет")
    assert sent == []


@pytest.mark.asyncio
async def test_the_reply_points_at_the_question_only_where_the_channel_can_draw_one():
    """M3: the live message of a turn quotes the message that started it."""
    from types import SimpleNamespace

    from nanomuse.channels.manager import ChannelManager

    manager = ChannelManager.__new__(ChannelManager)
    manager._answer_to = {
        "t1": ("telegram", "1001", "7"),
        "t2": ("fake", "1", "9"),
        "t3": ("telegram", "1001", "8"),
    }
    manager.channels = {
        "telegram": SimpleNamespace(supports_reply=True),
        "fake": SimpleNamespace(),
    }
    assert manager._reply_to("t1", "telegram", "1001") == "7"
    # another chat of the same channel is not the one being answered
    assert manager._reply_to("t3", "telegram", "2002") == ""
    # a channel that cannot show a reply link is not given an id it would reject
    assert manager._reply_to("t2", "fake", "1") == ""
    assert manager._reply_to("nothing", "telegram", "1001") == ""
    manager.routes = {"t1": ("telegram", "1001")}
    manager._live = {}
    assert manager._live_for("t1").reply_to == "7"


@pytest.mark.asyncio
async def test_the_step_stays_under_the_answer_and_the_step_name_restarts_the_indicator():
    """п.6 and п.7: a long job says what it is doing while the words are already there."""
    from types import SimpleNamespace

    from nanomuse.channels.manager import ChannelManager, _Live

    calls: list[tuple] = []

    class Ch:
        supports_action = True
        supports_reactions = True
        supports_edit = True

        async def typing(self, chat_id, on, action=""):
            calls.append(("typing", chat_id, on, action))

        async def react(self, chat_id, message_id, mark):
            calls.append(("react", chat_id, message_id, mark))

        async def stream(self, chat_id, stream_id, text, *, done, reply_to=""):
            calls.append(("stream", text, done))

    manager = ChannelManager.__new__(ChannelManager)
    manager.channels = {"telegram": Ch()}
    manager.routes = {"t1": ("telegram", "1001")}
    manager._typing = set()
    manager._typing_action = {}
    manager._answer_to = {"t1": ("telegram", "1001", "7")}
    manager._redact_outbound = lambda text: text
    manager._live = {"t1": _Live(channel="telegram", chat_id="1001", reply_to="7")}

    await manager._set_typing("t1", True, "photo")
    # the same step again is already shown; another step must be said
    await manager._set_typing("t1", True, "photo")
    await manager._set_typing("t1", True, "document")
    assert calls == [
        ("typing", "1001", True, "photo"),
        ("typing", "1001", True, "document"),
    ]

    live = manager._live["t1"]
    live.text = "готово наполовину"
    live.status = "Читаю файл"
    await manager._flush("t1")
    assert calls[-1] == ("stream", "готово наполовину\n\n_Читаю файл_", False)

    await manager._mark("t1", "working")
    await manager._mark("t1", "done")
    assert calls[-2:] == [
        ("react", "1001", "7", "working"),
        ("react", "1001", "7", "done"),
    ]
