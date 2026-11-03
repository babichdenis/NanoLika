"""The person's clock, not the process's: Russia spans eleven zones, so a relay in one city
must not decide what time it is for a person on the other side of the country."""

from __future__ import annotations

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
    line = next(l for l in text.splitlines() if l.startswith("- Current date/time:"))
    assert timeutil.now().strftime("%Y-%m-%d %H:00") in line
    assert "UTC+1000" in line
