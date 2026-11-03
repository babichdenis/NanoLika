"""The person's clock.

Everything a human reads (the "current date/time" line in the prompt, the time a reminder
is set for, "today" in a check-in) is in the *person's* timezone, never the process's. On a
computer that is the system zone; for a messenger bot the runtime may live on a relay in
another country, so the account's zone arrives with the shared profile and is kept here.

Set once, read everywhere: :func:`set_zone` from the account (IANA name), :func:`now` for
the clock, :func:`localize` for a stamp that was stored in UTC.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: the account's zone, when it is known (an IANA name); None = this computer's own zone
_zone: tzinfo | None = None
_zone_name: str = ""


def zone_name() -> str:
    """The IANA name in use: the account's when set, else the system's ("" if unplaceable)."""
    if _zone_name:
        return _zone_name
    return system_zone_name()


def system_zone_name() -> str:
    """This computer's zone as an IANA name: TZ when it is a name, else the localtime link."""
    raw = (os.environ.get("TZ") or "").strip()
    if raw and not raw.startswith(":") and _placeable(raw):
        return raw
    try:
        link = os.path.realpath("/etc/localtime")
    except OSError:
        link = ""
    marker = "zoneinfo/"
    if marker in link:
        name = link.split(marker, 1)[1]
        if _placeable(name):
            return name
    return ""


def _placeable(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def trusted() -> bool:
    """Whether the clock belongs to the person rather than to this machine.

    True when the account's zone is known (the computer wrote it) or when this process is
    the desktop app itself. A bot container with no account zone has no business stating a
    time: its own zone is the datacentre's, not the person's."""
    return bool(_zone_name) or os.environ.get("NANOMUSE_DESKTOP") == "1"


def set_zone(name: str) -> bool:
    """Adopt the account's zone. ``""`` or an unknown name goes back to the system zone.

    True when the zone is now the named one. Unknown names are ignored on purpose: a bad
    value from an older relay must not leave the person reading a wrong clock."""
    global _zone, _zone_name
    name = (name or "").strip()
    if not name:
        _zone, _zone_name = None, ""
        return False
    try:
        _zone = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    _zone_name = name
    return True


def zone() -> tzinfo:
    """The zone to show times in: the account's, else this computer's."""
    if _zone is not None:
        return _zone
    return datetime.now().astimezone().tzinfo or UTC


def now() -> datetime:
    """The person's current time, aware."""
    return datetime.now(UTC).astimezone(zone())


def localize(value: datetime) -> datetime:
    """A stored stamp in the person's zone (naive stamps are read as UTC, the way the
    reminders store them)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(zone())


def today():
    """The person's current date."""
    return now().date()


def stamp(fmt: str = "%Y-%m-%d %H:%M") -> str:
    """The person's clock as text, for prompts and reminders."""
    return now().strftime(fmt)


__all__ = [
    "localize",
    "now",
    "set_zone",
    "trusted",
    "stamp",
    "system_zone_name",
    "today",
    "zone",
    "zone_name",
]
