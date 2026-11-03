"""Whose clock is whose: a desktop tells the account its zone, a relay box in UTC adopts it."""

from __future__ import annotations

import asyncio

import pytest

from nanomuse import timeutil

# the service package imports the hub and back; the app imports them in this order
import nanomuse.server.service  # noqa: F401
from nanomuse.hub.service import HubService


class FakeCloud:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def put_tz(self, tz: str) -> None:
        self.sent.append(tz)


@pytest.fixture(autouse=True)
def _reset():
    timeutil.set_zone("")
    yield
    timeutil.set_zone("")


def hub() -> HubService:
    h = HubService.__new__(HubService)
    h._tz_sent = False
    h.cloud = FakeCloud()  # type: ignore[assignment]
    return h


def test_a_relay_box_in_utc_adopts_the_account_s_clock(monkeypatch) -> None:
    monkeypatch.setattr(timeutil, "system_zone_name", lambda: "UTC")
    h = hub()
    asyncio.run(h._sync_clock({"account": {"tz": "Asia/Vladivostok"}}))
    assert timeutil.zone_name() == "Asia/Vladivostok"
    assert timeutil.now().utcoffset().total_seconds() == 10 * 3600
    assert h.cloud.sent == []  # a box with no clock of its own has nothing to teach


def test_a_desktop_teaches_the_account_its_zone_once(monkeypatch) -> None:
    monkeypatch.setattr(timeutil, "system_zone_name", lambda: "Europe/Moscow")
    h = hub()
    asyncio.run(h._sync_clock({"account": {"tz": "Asia/Vladivostok"}}))
    assert h.cloud.sent == ["Europe/Moscow"]
    assert timeutil.zone_name() == "Europe/Moscow"  # a laptop keeps its own clock
    asyncio.run(h._sync_clock({"account": {"tz": "Asia/Vladivostok"}}))
    assert h.cloud.sent == ["Europe/Moscow"]  # once per process, no ping-pong


def test_an_empty_account_zone_leaves_the_desktop_alone(monkeypatch) -> None:
    monkeypatch.setattr(timeutil, "system_zone_name", lambda: "Europe/Moscow")
    h = hub()
    asyncio.run(h._sync_clock({"account": {}}))
    assert h.cloud.sent == ["Europe/Moscow"]
    assert timeutil.zone_name() == "Europe/Moscow"


def test_an_unknown_zone_from_an_old_relay_is_ignored(monkeypatch) -> None:
    monkeypatch.setattr(timeutil, "system_zone_name", lambda: "UTC")
    h = hub()
    asyncio.run(h._sync_clock({"account": {"tz": "Mars/Olympus"}}))
    assert timeutil.zone_name() == "UTC"
