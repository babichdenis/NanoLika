"""A chat that spends its own Cloud account, and a refusal the chat can hear."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from nanomuse.channels import manager as manager_module
from nanomuse.channels.base import Channel, ChannelStatus, Field, InboundMessage
from nanomuse.channels.manager import ChannelManager, thread_id_for
from nanomuse.config import Settings
from nanomuse.llm import MockLLM
from nanomuse.server import create_app
from nanomuse.server.service import MuseService


def run(manager: ChannelManager, coro):  # noqa: ANN001, ANN201
    """Run a coroutine on the manager's own loop, which lives in its own thread."""
    assert manager._loop is not None
    return asyncio.run_coroutine_threadsafe(coro, manager._loop).result(timeout=10)


def emit(manager: ChannelManager, message: dict[str, Any]) -> None:
    assert manager._loop is not None
    manager._loop.call_soon_threadsafe(manager.svc.bus.publish, message)


def wait_for(pred, timeout: float = 8.0, interval: float = 0.03):  # noqa: ANN001
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = pred()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met in time")


class QuietChannel(Channel):
    name = "quiet"
    label = "Quiet"
    supports_edit = True
    console_url = "https://example.invalid/console"
    fields = (Field("token", "Token", "secret", required=True),)
    instances: list[QuietChannel] = []

    def __init__(self, settings: dict[str, Any]):
        super().__init__(settings)
        self.sent: list[tuple[str, str]] = []
        QuietChannel.instances.append(self)

    async def start(self, bus) -> None:  # noqa: ANN001
        await super().start(bus)
        bus.set_status(self.name, "connected", "quiet bot")

    async def stop(self) -> None:
        return None

    async def send(self, chat_id: str, text: str, *, files: list[str] | None = None) -> str:
        self.sent.append((chat_id, text))
        return "m1"

    async def stream(self, chat_id: str, stream_id: str, text: str, *, done: bool) -> None:
        if done:
            self.sent.append((chat_id, text))

    async def verify(self) -> str:
        return "Bot: quiet"


class Door:
    """The relay's bot door, answering what the test says."""

    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = (
            body
            if body is not None
            else {"api_key": "key-from-the-door", "models": [{"id": "deepseek-flash"}]}
        )
        self.calls: list[dict[str, Any]] = []

    def client(self, real: Any) -> Any:
        """A factory that answers from this door, for patching `httpx.AsyncClient`."""

        def build(*_args: Any, **_kwargs: Any) -> httpx.AsyncClient:
            return real(transport=httpx.MockTransport(self.handle))

        return build

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(
            {
                "url": str(request.url),
                "body": json.loads(request.content or b"{}"),
                "token": request.headers.get("X-Bot-Token", ""),
            }
        )
        return httpx.Response(self.status, json=self.body)


@pytest.fixture()
def chan(settings: Settings) -> Iterator[tuple[MuseService, ChannelManager, QuietChannel]]:
    settings.server.token = "secret-token"
    settings.agent.language = "en"
    service = MuseService(settings, llm=MockLLM([]))
    app = create_app(settings, service)
    manager: ChannelManager = service.channels  # type: ignore[attr-defined]
    manager.types["quiet"] = QuietChannel
    manager.status["quiet"] = ChannelStatus()
    # the settings go in whole: `update` keeps the channel's declared fields only, and the
    # Cloud ones are free-form settings a person writes in config.toml
    manager.store.update(QuietChannel, enabled=True, values={"token": "t0k"})
    data = dict(manager.store._file.load())
    entry = dict(data.get("quiet") or {})
    settings_in = dict(entry.get("settings") or {})
    settings_in.update(
        {
            "cloud_per_chat": "true",
            "cloud_url": "https://cloud.test",
            "cloud_bot_token": "bot-secret",
        }
    )
    entry["settings"] = settings_in
    data["quiet"] = entry
    manager.store._file.save(data)
    QuietChannel.instances.clear()
    with TestClient(app):
        wait_for(lambda: manager.status["quiet"].state == "connected")
        yield service, manager, QuietChannel.instances[0]


def message(
    text: str = "привет", sender: str = "700100200", chat: str = "700100200"
) -> InboundMessage:
    return InboundMessage(
        channel="quiet", chat_id=chat, sender_id=sender, sender_name="Ann", text=text
    )


def test_a_chat_without_the_settings_runs_on_the_configured_model(chan):
    _service, manager, _fake = chan
    data = dict(manager.store._file.load())
    entry = dict(data.get("quiet") or {})
    settings = dict(entry.get("settings") or {})
    settings.pop("cloud_per_chat", None)
    entry["settings"] = settings
    data["quiet"] = entry
    manager.store._file.save(data)
    assert run(manager, manager._cloud_key_for("quiet", message())) == ("", "")


def test_the_door_opens_an_account_for_the_chat_and_the_key_is_kept(chan, monkeypatch):
    _service, manager, fake = chan
    door = Door()
    real = httpx.AsyncClient
    monkeypatch.setattr(manager_module.httpx, "AsyncClient", door.client(real))
    key, model = run(manager, manager._cloud_key_for("quiet", message()))
    assert (key, model) == ("key-from-the-door", "deepseek-flash")
    assert door.calls[0]["url"] == "https://cloud.test/v1/bot/signin"
    assert door.calls[0]["token"] == "bot-secret"
    assert door.calls[0]["body"]["telegram_id"] == "700100200"
    # a second message inside the ten minutes does not sign in again
    again = run(manager, manager._cloud_key_for("quiet", message("ещё")))
    assert again == (key, model) and len(door.calls) == 1
    # once it lapses the door is asked again
    manager._cloud_keys[("quiet", "700100200")] = (key, model, time.time() - 1)
    run(manager, manager._cloud_key_for("quiet", message("третье")))
    assert len(door.calls) == 2


def test_a_closed_door_is_said_in_the_chat_and_nothing_runs(chan, monkeypatch):
    _service, manager, fake = chan
    door = Door(status=403, body={"error": {"code": "signup_closed", "message": "no"}})
    real = httpx.AsyncClient
    monkeypatch.setattr(manager_module.httpx, "AsyncClient", door.client(real))
    key, _model = run(manager, manager._cloud_key_for("quiet", message()))
    assert key == "!", "the turn must not run on this machine's model"
    assert fake.sent, "the chat has to hear why"
    assert "did not open an account" in fake.sent[-1][1]


def test_an_exhausted_allowance_is_said_in_the_chat(chan):
    _service, manager, fake = chan
    thread = thread_id_for("quiet", "700100200")
    manager.routes[thread] = ("quiet", "700100200")
    emit(
        manager,
        {
            "kind": "event",
            "event": {
                "type": "notice",
                "level": "error",
                "code": "allowance_exhausted",
                "text": "The free allowance is used up.",
                "thread": thread,
            },
        },
    )
    text = wait_for(lambda: fake.sent and fake.sent[-1][1])
    assert "used up" in text
