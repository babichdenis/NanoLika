"""The person's clock, not the process's: Russia spans eleven zones, so a relay in one city
must not decide what time it is for a person on the other side of the country."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from nanomuse import timeutil


@pytest.fixture(autouse=True)
def _reset():
    timeutil.set_zone("")
    yield
    timeutil.set_zone("")


def test_this_box_knows_its_own_zone() -> None:
    assert timeutil.system_zone_name()
    assert timeutil.now().tzinfo is not None


def test_the_account_zone_wins_and_a_bad_name_is_ignored() -> None:
    assert timeutil.set_zone("Asia/Vladivostok") is True
    assert timeutil.now().utcoffset().total_seconds() == 10 * 3600
    assert timeutil.set_zone("Mars/Olympus") is False
    assert timeutil.zone_name() == "Asia/Vladivostok"  # the old one stays
    assert timeutil.set_zone("Europe/Kaliningrad") is True
    assert timeutil.now().utcoffset().total_seconds() == 2 * 3600


def test_a_stored_stamp_is_read_on_the_person_s_clock() -> None:
    timeutil.set_zone("Asia/Vladivostok")
    stamp = datetime(2026, 11, 3, 19, 0, tzinfo=UTC)  # 19:00 UTC = 05:00 next day there
    assert timeutil.localize(stamp).strftime("%Y-%m-%d %H:%M") == "2026-11-04 05:00"
    # a naive stamp is UTC, the way the reminders store keeps them
    assert timeutil.localize(datetime(2026, 11, 3, 19, 0)).strftime("%H:%M") == "05:00"


def test_the_prompt_says_the_person_s_time(settings) -> None:
    """The line the model reads is the clock of the person, with its own offset."""
    from test_agent import make_agent

    timeutil.set_zone("Asia/Vladivostok")
    agent, *_ = make_agent(settings, [])
    text = agent.build_system_prompt("hi")
    line = next(row for row in text.splitlines() if row.startswith("- Current date/time:"))
    assert timeutil.now().strftime("%Y-%m-%d, %H:00") in line
    assert "Asia/Vladivostok" in line


@pytest.mark.asyncio
async def test_shared_bot_keeps_account_clocks_separate_across_concurrent_tasks(monkeypatch):
    from nanomuse.memory.scope import memory_scope

    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    timeutil.set_zone("Europe/Moscow")  # the bot owner's clock must not leak to clients
    assert timeutil.set_account_zone("account:timezone-test-a", "Europe/Kaliningrad")
    assert timeutil.set_account_zone("account:timezone-test-b", "Asia/Vladivostok")
    barrier = asyncio.Event()
    started = 0

    async def read_clock(scope):
        nonlocal started
        with memory_scope(scope):
            started += 1
            if started == 2:
                barrier.set()
            await barrier.wait()
            await asyncio.sleep(0)
            return timeutil.zone_name(), timeutil.now().utcoffset(), timeutil.trusted()

    a, b = await asyncio.gather(read_clock("account:timezone-test-a"), read_clock("account:timezone-test-b"))
    assert a[0] == "Europe/Kaliningrad" and a[1].total_seconds() == 2 * 3600 and a[2]
    assert b[0] == "Asia/Vladivostok" and b[1].total_seconds() == 10 * 3600 and b[2]
    with memory_scope("account:unknown"):
        assert not timeutil.trusted()
        assert timeutil.zone_name() != "Europe/Moscow" or timeutil.system_zone_name() == "Europe/Moscow"
    with memory_scope("installation"):
        assert not timeutil.trusted()
    assert not timeutil.set_account_zone("channel:unverified", "Asia/Vladivostok")
    assert not timeutil.set_account_zone("account:timezone-test-a", "Mars/Olympus")
    with memory_scope("account:timezone-test-a"):
        assert not timeutil.trusted()


def test_personal_installation_keeps_its_existing_clock(monkeypatch):
    from nanomuse.memory.scope import memory_scope

    monkeypatch.delenv("NANOMUSE_SHARED_CLOUD_BOT", raising=False)
    assert timeutil.set_zone("Europe/Kaliningrad")
    assert timeutil.set_account_zone("account:desktop-test", "Asia/Vladivostok")
    with memory_scope("account:desktop-test"):
        assert timeutil.zone_name() == "Europe/Kaliningrad"
        assert timeutil.trusted()


def test_shared_agent_prompt_uses_only_its_customers_clock(settings, monkeypatch):
    from test_agent import make_agent

    from nanomuse.memory.scope import memory_scope

    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    timeutil.set_zone("Europe/Moscow")
    timeutil.set_account_zone("account:prompt-clock-a", "Europe/Kaliningrad")
    timeutil.set_account_zone("account:prompt-clock-b", "Asia/Vladivostok")
    agent, *_ = make_agent(settings, [])
    with memory_scope("account:prompt-clock-a"):
        prompt_a = agent.build_system_prompt("hello")
    with memory_scope("account:prompt-clock-b"):
        prompt_b = agent.build_system_prompt("hello")
    with memory_scope("account:prompt-clock-unknown"):
        unknown = agent.build_system_prompt("hello")
    line_a = next(line for line in prompt_a.splitlines() if line.startswith("- Current date/time:"))
    line_b = next(line for line in prompt_b.splitlines() if line.startswith("- Current date/time:"))
    line_unknown = next(line for line in unknown.splitlines() if line.startswith("- Current date/time:"))
    assert "Europe/Kaliningrad" in line_a and "Asia/Vladivostok" not in line_a
    assert "Asia/Vladivostok" in line_b and "Europe/Kaliningrad" not in line_b
    assert "not known" in line_unknown and "Europe/Moscow" not in line_unknown
