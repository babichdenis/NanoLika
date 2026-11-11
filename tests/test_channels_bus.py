"""The long-lived messenger pump must recover after a full fan-out queue."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from nanomuse.channels.manager import CHANNEL_EVENT_KINDS, ChannelManager
from nanomuse.server.events import EventBus


@pytest.mark.asyncio
async def test_channel_pump_resubscribes_after_bus_overflow() -> None:
    bus = EventBus()
    manager = ChannelManager.__new__(ChannelManager)
    manager.svc = SimpleNamespace(bus=bus)
    manager._queue = bus.subscribe()
    entered = asyncio.Event()
    release = asyncio.Event()
    caught_up = asyncio.Event()
    delivered: list[int] = []

    async def route(message: dict) -> None:
        if message["number"] == -1:
            entered.set()
            await release.wait()
        delivered.append(message["number"])
        if message["number"] == 1001:
            caught_up.set()

    manager._route = route
    pump = asyncio.create_task(manager._pump_loop())
    try:
        bus.publish({"number": -1})
        await asyncio.wait_for(entered.wait(), 2)
        for number in range(1000):
            bus.publish({"number": number})
        bus.publish({"number": 1000})  # the full subscriber is detached here
        assert bus.connections == 0
        release.set()
        for _ in range(100):
            if bus.connections:
                break
            await asyncio.sleep(0.01)
        assert bus.connections == 1
        bus.publish({"number": 1001})
        await asyncio.wait_for(caught_up.wait(), 2)
        # The original buffered sequence stays ordered. The overflow event itself
        # was lost: restoring the live subscription is not a delivery guarantee.
        assert delivered == [-1, *range(1000), 1001]
    finally:
        pump.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pump
        bus.unsubscribe(manager._queue)


@pytest.mark.asyncio
async def test_regular_slow_websocket_subscriber_remains_detached() -> None:
    bus = EventBus()
    queue = bus.subscribe()
    for number in range(1001):
        bus.publish({"number": number})
    assert bus.connections == 0
    assert queue.qsize() == 1000


@pytest.mark.asyncio
async def test_channel_subscription_filters_ignored_events_and_retains_filter_after_restore() -> None:
    bus = EventBus()
    queue = bus.subscribe(lambda msg: msg.get("kind") in CHANNEL_EVENT_KINDS)
    for _ in range(2000):
        bus.publish({"kind": "calendar"})
    assert bus.connections == 1 and queue.empty()
    for number in range(1001):
        bus.publish({"kind": "event", "number": number})
    assert bus.connections == 0 and queue.qsize() == 1000
    assert (await queue.get())["number"] == 0
    assert bus.restore(queue)
    bus.publish({"kind": "calendar"})
    bus.publish({"kind": "event", "number": 1001})
    assert queue.qsize() == 1000
    assert (await queue.get())["number"] == 1
    bus.unsubscribe(queue)
    assert bus.connections == 0
