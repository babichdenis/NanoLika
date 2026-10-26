"""The six characters an app shows and a chat types (link_code.py), from the bot's side.

The relay is faked here: what these tests hold the bot to is that only a message which is
nothing but a code is read as one, that a chat already belonging to an account keeps its
ordinary messages, and that the words the person gets back say what happened.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from nanomuse.channels.base import InboundMessage
from nanomuse.channels.bot_copy import channel_text
from nanomuse.channels.link_code import ChatLink, answer_code, as_code
from nanomuse.channels.manager import ChannelManager
from nanomuse.channels.max import MaxChannel
from nanomuse.channels.telegram import TelegramChannel

CODE = "ABCDEF"
CHAT = 1001


class Bus:
    """The manager, as much of it as a channel uses."""

    def __init__(self, lang: str = "ru") -> None:
        self.lang = lang
        self.inbound_msgs: list[Any] = []
        self.statuses: list[tuple[str, str, str]] = []
        self.actions: list[tuple[str, str, str, dict[str, Any]]] = []
        # what the channel asked the menu to show: the words and the code of the question
        # "this chat belongs to that account, leave it?" — the manager's own job is tested
        # in test_channel_menu.py, this only checks that the question is asked at all
        self.switch: tuple[str, str] | None = None
        self.menu = self

    async def switch_ask(self, msg: Any, text: str, code: str) -> None:
        self.switch = (text, code)

    def inbound(self, msg: Any) -> None:
        self.inbound_msgs.append(msg)

    def set_status(self, channel: str, state: str, detail: str = "") -> None:
        self.statuses.append((channel, state, detail))

    def action(self, channel: str, chat_id: str, sender_id: str, value: dict[str, Any]) -> None:
        self.actions.append((channel, chat_id, sender_id, value))


class FakeRelay:
    """The relay's two linking routes, and the Bot API's sending side in the same transport."""

    def __init__(self, status: dict[str, Any] | None = None, link: tuple[int, dict] | None = None):
        self.status = status if status is not None else {"linked": False, "channel": "telegram"}
        self.link = link if link is not None else (200, {"linked": True, "channel": "telegram"})
        self.unlink: tuple[int, dict] = (
            200,
            {"unlinked": True, "channel": "telegram", "reason": ""},
        )
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        # what each relay call said in its query string, beside the body it posted
        self.params: list[dict[str, Any]] = []
        self.sent: list[str] = []
        self.bot_api_down = False
        self.next_message_id = 500

    async def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "relay.test":
            body: dict[str, Any] = {}
            if request.content:
                import json

                body = json.loads(request.content)
            self.calls.append((request.method, request.url.path, body))
            self.params.append(dict(request.url.params))
            if request.url.path == "/v1/bot/link" and request.method == "GET":
                return httpx.Response(200, json=self.status)
            if request.url.path == "/v1/bot/link" and request.method == "POST":
                status, payload = self.link
                return httpx.Response(status, json=payload)
            if request.url.path == "/v1/bot/unlink" and request.method == "POST":
                status, payload = self.unlink
                return httpx.Response(status, json=payload)
            return httpx.Response(404, json={"error": {"code": "not_found"}})
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "sendMessage":
            body = {}
            if request.content:
                import json

                body = json.loads(request.content)
            self.sent.append(str(body.get("text") or ""))
            self.next_message_id += 1
            return httpx.Response(
                200, json={"ok": True, "result": {"message_id": self.next_message_id}}
            )
        if method == "getMe":
            return httpx.Response(
                200, json={"ok": True, "result": {"id": 42, "username": "muse_bot"}}
            )
        return httpx.Response(200, json={"ok": True, "result": True})


def channel(relay: FakeRelay, **settings: Any) -> TelegramChannel:
    return TelegramChannel(
        {
            "bot_token": "123:abc",
            "cloud_url": "http://relay.test",
            "cloud_bot_token": "bot-secret",
            **settings,
        },
        transport=httpx.MockTransport(relay.handler),
    )


def update(text: str, sender: dict[str, Any] | None = None, group: bool = False) -> dict[str, Any]:
    chat = {"id": -500, "type": "supergroup"} if group else {"id": CHAT, "type": "private"}
    return {
        "update_id": 1,
        "message": {
            "message_id": 7,
            "from": sender or {"id": CHAT, "first_name": "Денис", "username": "denis"},
            "chat": chat,
            "text": text,
        },
    }


async def until(pred, timeout: float = 3.0) -> None:  # noqa: ANN001
    for _ in range(int(timeout / 0.02)):
        if pred():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met in time")


def test_a_message_is_a_code_only_when_it_is_nothing_else():
    assert as_code("ABCDEF") == "ABCDEF"
    assert as_code("  abcdef \n") == "ABCDEF"
    assert as_code("234567") == "234567"
    # a command, a word, a sentence with a code inside it, the look-alike letters
    for not_a_code in (
        "/start",
        "hello",
        "code ABCDEF",
        "ABCDE",
        "ABCDEFG",
        "ABCDE0",
        "АБВГДЕ",
        "",
    ):
        assert as_code(not_a_code) == ""


def test_without_a_relay_the_feature_is_simply_off():
    assert ChatLink("telegram", {}).configured() is False
    assert ChatLink("telegram", {"cloud_url": "http://relay.test"}).configured() is False
    off = ChatLink("telegram", {"cloud_url": "http://relay.test", "cloud_bot_token": "s"})
    assert off.configured() is True


async def test_a_code_from_a_chat_with_no_account_links_it():
    relay = FakeRelay()
    bus = Bus()
    ch = channel(relay)
    await ch.start(bus)
    await ch._handle_update(update(CODE.lower()))
    assert [m for m, _p, _b in relay.calls] == ["POST"]
    posted = [b for m, p, b in relay.calls if m == "POST"][0]
    assert posted == {
        "channel": "telegram",
        "chat_id": str(CHAT),
        "code": CODE,
        "identifier": "@denis",
    }
    assert relay.sent == [channel_text("ru", "link_linked")]
    # nothing was handed to the Muse: a code is an instruction, not a message
    assert bus.inbound_msgs == []
    await ch.stop()


async def test_a_code_for_a_chat_that_already_has_an_account_asks_about_leaving():
    """It used to be silence: the code was never even shown to the relay, so a person holding
    a fresh QR had nowhere to put it. It is a question now, in two buttons."""
    relay = FakeRelay(status={"linked": True, "channel": "telegram", "identifier": "@denis"})
    relay.link = (
        409,
        {
            "error": {
                "message": "already has an account",
                "code": "link_taken",
                "taken": {"label": "ba***@gmail.com", "own": False},
            }
        },
    )
    bus = Bus()
    ch = channel(relay)
    await ch.start(bus)
    await ch._handle_update(update(CODE))
    await until(lambda: bus.switch or relay.sent)
    assert bus.switch, "the answer must be a question with two buttons"
    text, code = bus.switch
    assert "ba***@gmail.com" in text
    assert code == CODE.upper()
    await ch.stop()


async def test_an_ordinary_message_never_asks_the_relay():
    relay = FakeRelay()
    bus = Bus()
    ch = channel(relay)
    await ch.start(bus)
    await ch._handle_update(update("привет, как дела?"))
    await until(lambda: bus.inbound_msgs)
    assert bus.inbound_msgs[0].text == "привет, как дела?"
    # a command is a command, even one that looks like six characters
    await ch._handle_update(update("/ABCDEF"))
    await until(lambda: len(bus.inbound_msgs) == 2)
    assert relay.calls == []
    await ch.stop()


async def test_a_group_message_is_never_read_as_a_code():
    relay = FakeRelay()
    bus = Bus()
    ch = channel(relay)
    await ch.start(bus)
    await ch._handle_update(update(CODE, group=True))
    await until(lambda: bus.inbound_msgs)
    assert relay.calls == [] and bus.inbound_msgs[0].is_group
    await ch.stop()


async def test_a_wrong_code_says_so_and_leaves_the_message_out():
    relay = FakeRelay(link=(400, {"error": {"code": "link_wrong", "message": "nope"}}))
    bus = Bus()
    ch = channel(relay)
    await ch.start(bus)
    await ch._handle_update(update(CODE))
    assert relay.sent == [channel_text("ru", "link_wrong")]
    assert bus.inbound_msgs == []
    await ch.stop()


async def test_a_chat_with_an_account_of_its_own_is_told_so():
    relay = FakeRelay(link=(409, {"error": {"code": "link_taken", "message": "taken"}}))
    bus = Bus()
    ch = channel(relay)
    await ch.start(bus)
    await ch._handle_update(update(CODE))
    assert relay.sent == [channel_text("ru", "link_taken")]
    assert bus.inbound_msgs == []
    await ch.stop()


async def test_a_relay_that_does_not_answer_hands_the_message_on():
    relay = FakeRelay(link=(500, {"error": {"code": "offline"}}))
    bus = Bus()
    ch = channel(relay)
    await ch.start(bus)
    await ch._handle_update(update(CODE))
    assert relay.sent == [channel_text("ru", "link_offline")]
    assert bus.inbound_msgs == []
    await ch.stop()


async def test_a_relay_that_is_not_there_at_all_changes_nothing():
    relay = FakeRelay(status={"linked": False})
    bus = Bus()
    ch = channel(relay, cloud_url="")
    await ch.start(bus)
    await ch._handle_update(update(CODE))
    await until(lambda: bus.inbound_msgs)
    assert bus.inbound_msgs[0].text == CODE
    assert relay.calls == [] and relay.sent == []
    await ch.stop()


async def test_an_unreachable_relay_leaves_an_ordinary_message_alone():
    bus = Bus()

    relay = FakeRelay()

    async def refuse(request: httpx.Request) -> httpx.Response:
        if request.url.host == "relay.test":
            raise httpx.ConnectError("no relay", request=request)
        return await relay.handler(request)

    ch = TelegramChannel(
        {"bot_token": "123:abc", "cloud_url": "http://relay.test", "cloud_bot_token": "s"},
        transport=httpx.MockTransport(refuse),
    )
    await ch.start(bus)
    await ch._handle_update(update(CODE))
    # The person is told the server did not answer, and the six characters do not go on to the
    # model: a code is a one-time secret, and a prompt is the last place it should end up. The
    # reply is the whole of its answer.
    assert bus.inbound_msgs == []
    assert len(relay.sent) == 1 and "не ответил" in relay.sent[0]
    await ch.stop()


async def test_the_status_alone_answers_whether_a_chat_is_somebodys():
    relay = FakeRelay(status={"linked": False, "channel": "telegram"})
    link = ChatLink(
        "telegram",
        {"cloud_url": "http://relay.test", "cloud_bot_token": "s"},
        httpx.MockTransport(relay.handler),
    )
    assert await link.status(str(CHAT)) == {"linked": False, "channel": "telegram"}
    assert relay.calls == [("GET", "/v1/bot/link", {})]
    # a word is not worth a call
    assert await answer_code(link, str(CHAT), "привет") == ""
    assert len(relay.calls) == 1


async def test_the_max_bot_answers_a_code_too():
    relay = FakeRelay()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "relay.test":
            return await relay.handler(request)
        # MAX: sending a message back
        if request.url.path == "/messages":
            body = {}
            if request.content:
                import json

                body = json.loads(request.content)
            relay.sent.append(str(body.get("text") or ""))
            return httpx.Response(200, json={"success": True, "message": {"body": {"mid": "m1"}}})
        return httpx.Response(200, json={"success": True})

    bus = Bus()
    ch = MaxChannel(
        {"bot_token": "t", "cloud_url": "http://relay.test", "cloud_bot_token": "s"},
        transport=httpx.MockTransport(handler),
    )
    ch._http = ch._make_http()
    ch.bus = bus
    ch.handle_update(
        {
            "update_type": "message_created",
            "message": {
                "sender": {"user_id": CHAT, "name": "Денис"},
                "recipient": {"chat_id": "77", "chat_type": "dialog"},
                "body": {"mid": "m2", "text": CODE},
            },
        }
    )
    await until(lambda: relay.sent)
    assert relay.sent == [channel_text("ru", "link_linked")]
    assert bus.inbound_msgs == []
    assert [m for m, _p, _b in relay.calls] == ["POST"]
    # the messenger this bot is, and the id under the neutral name: the relay keys a chat's
    # account on the pair, and a MAX chat is not a Telegram one. The body carries it — there is
    # no status GET any more, since every code is offered to the relay.
    posted = [b for m, _p, b in relay.calls if m == "POST"][0]
    assert posted["channel"] == "max"
    assert posted["chat_id"] == str(CHAT)
    assert "telegram_id" not in posted
    await ch._http.aclose()


@pytest.mark.parametrize("surface", ["telegram", "max"])
async def test_every_link_call_carries_the_messenger_it_wrote_from(surface: str):
    """Both bots say which messenger they are on both calls, and neither calls its id a
    Telegram one: the same digits on the two messengers are two people at the relay."""
    relay = FakeRelay()
    link = ChatLink(
        surface,
        {"cloud_url": "http://relay.test", "cloud_bot_token": "s"},
        httpx.MockTransport(relay.handler),
    )
    await link.status(str(CHAT))
    await link.redeem(str(CHAT), CODE, "@denis")
    # the status asks in the query string, the link posts a body: the messenger is in both
    assert relay.params == [{"channel": surface, "chat_id": str(CHAT)}, {}]
    posted = [b for m, _p, b in relay.calls if m == "POST"][0]
    assert posted == {
        "channel": surface,
        "chat_id": str(CHAT),
        "code": CODE,
        "identifier": "@denis",
    }


def test_a_channel_that_names_no_messenger_is_a_telegram_one():
    assert ChatLink("", {}).channel == "telegram"
    assert ChatLink("MAX", {}).channel == "max"


@pytest.mark.parametrize("text", ["ABCDEF", "ZXY234"])
async def test_a_code_is_asked_about_once_and_linked_once(text: str):
    relay = FakeRelay()
    bus = Bus()
    ch = channel(relay)
    await ch.start(bus)
    await ch._handle_update(update(text))
    assert [m for m, _p, _b in relay.calls] == ["POST"]
    await ch.stop()


# -- the way out: `/unlink` and ChatLink.release --------------------------------------------


@pytest.mark.parametrize(
    "reply,expected_key",
    [
        ((200, {"unlinked": True, "channel": "telegram", "reason": ""}), "unlink_done"),
        ((200, {"unlinked": True, "channel": "telegram", "reason": "account_gone"}), "unlink_done"),
        (
            (200, {"unlinked": False, "channel": "telegram", "reason": "channel_is_the_account"}),
            "unlink_is_account",
        ),
        (
            (200, {"unlinked": False, "channel": "telegram", "reason": "not_linked"}),
            "unlink_not_linked",
        ),
        ((500, {"error": {"code": "boom"}}), "unlink_offline"),
    ],
)
async def test_release_says_what_the_relay_said(reply, expected_key):
    """Every relay answer becomes words a person can act on, and never an exception."""
    relay = FakeRelay()
    relay.unlink = reply
    link = ChatLink(
        "telegram",
        {"cloud_url": "http://relay.test", "cloud_bot_token": "s"},
        httpx.MockTransport(relay.handler),
    )
    answer = await link.release(str(CHAT), "ru")
    assert answer == channel_text("ru", expected_key)
    assert [p for m, p, _b in relay.calls if m == "POST"] == ["/v1/bot/unlink"]


async def test_release_without_a_relay_says_so_instead_of_raising():
    link = ChatLink("telegram", {}, httpx.MockTransport(FakeRelay().handler))
    assert await link.release(str(CHAT), "ru") == channel_text("ru", "unlink_offline")


async def test_unlink_asks_before_it_does_anything():
    """A typed command that is easy to press twice must not unlink on the first word."""
    relay = FakeRelay()
    manager = FakeManager(relay)
    msg = InboundMessage(channel="telegram", chat_id=str(CHAT), sender_id=str(CHAT), text="/unlink")

    await manager._unlink_chat(msg)
    # the question, and nothing sent to the relay
    assert manager.sent == [channel_text("ru", "unlink_confirm")]
    assert relay.calls == []

    await manager._unlink_chat(msg)
    # the confirmation, and the relay heard once
    assert manager.sent[-1] == channel_text("ru", "unlink_done")
    assert [p for m, p, _b in relay.calls if m == "POST"] == ["/v1/bot/unlink"]


async def test_unlink_is_asked_again_after_the_window():
    """A stale "yes" from ten minutes ago must not count as the confirmation."""
    relay = FakeRelay()
    manager = FakeManager(relay)
    msg = InboundMessage(channel="telegram", chat_id=str(CHAT), sender_id=str(CHAT), text="/unlink")
    await manager._unlink_chat(msg)
    manager.age_the_question()
    await manager._unlink_chat(msg)
    assert manager.sent[-1] == channel_text("ru", "unlink_confirm")
    assert relay.calls == []


class FakeManager:
    """As much of `ChannelManager` as `/unlink` touches: the channel, `_send`, the language,
    and the pending-question book. The real class is exercised end to end elsewhere; what
    matters here is the ask-then-act shape and that nothing reaches the relay before it."""

    def __init__(self, relay: FakeRelay, channel_name: str = "telegram") -> None:
        self.relay = relay
        self.lang = "ru"
        self.sent: list[str] = []
        self._unlink_pending: dict[tuple[str, str], float] = {}
        settings = {
            "bot_token": "123:abc",
            "cloud_url": "http://relay.test",
            "cloud_bot_token": "bot-secret",
        }
        transport = httpx.MockTransport(relay.handler)
        gateway = ChatLink(channel_name, settings, transport)

        class Channel:
            """A channel as the manager sees it: one public `chat_link()`, nothing else."""

            name = channel_name

            @staticmethod
            def chat_link() -> ChatLink:
                return gateway

        self.channels = {channel_name: Channel()}

    async def _send(self, channel: str, chat_id: str, text: str) -> None:
        self.sent.append(text)

    def age_the_question(self) -> None:
        """Make the pending ask look older than the window, without waiting two minutes."""
        from nanomuse.channels import manager as manager_module

        for key in list(self._unlink_pending):
            self._unlink_pending[key] -= manager_module.UNLINK_CONFIRM_SECONDS + 1

    async def _unlink_chat(self, msg: InboundMessage) -> None:
        await ChannelManager._unlink_chat(self, msg)  # type: ignore[arg-type]
