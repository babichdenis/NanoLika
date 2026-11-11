"""The person's clock.

Everything a human reads (the "current date/time" line in the prompt, the time a reminder
is set for, "today" in a check-in) is in the *person's* timezone, never the process's. On a
computer that is the system zone; for a messenger bot the runtime may live on a relay in
another country, so the account's zone arrives with the shared profile and is kept here.

On a personal installation :func:`set_zone` sets the local account's clock. The shared
bot instead resolves the current task's account scope and its relay-verified timezone, so
one person's clock cannot change another's. :func:`now` returns the clock and
:func:`localize` converts a stamp stored in UTC.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: the account's zone, when it is known (an IANA name); None = this computer's own zone
_zone: tzinfo | None = None
_zone_name: str = ""
_account_zones: dict[str, tuple[str, tzinfo]] = {}
_account_zones_lock = threading.RLock()
_runtime_account: ContextVar[str] = ContextVar("nanomuse_runtime_account", default="")


@contextmanager
def account_runtime(account_id: str) -> Iterator[None]:
    """Carry the verified cloud-worker owner through requests and spawned tasks.

    A conversation's memory scope can intentionally be legacy-session; it must not
    decide which account clock to show for a worker serving multiple accounts.
    """
    token = _runtime_account.set(str(account_id or "").strip())
    try:
        yield
    finally:
        _runtime_account.reset(token)


def runtime_account() -> str:
    """The worker's verified account id in this task, or empty outside a worker."""
    return _runtime_account.get()


def shared_bot() -> bool:
    """The public bot has many owners and must not use a process-wide account zone."""
    return os.environ.get("NANOMUSE_SHARED_CLOUD_BOT") == "1"


def set_account_zone(scope: str, name: str) -> bool:
    """Record the zone supplied by the relay for a verified account namespace.

    An absent or invalid zone clears the previous value: a new unknown value must not
    silently inherit another person's clock. Arbitrary conversation/group scopes are not
    sufficient evidence of an account's identity.
    """
    scope = str(scope or "").strip()
    if not scope.startswith("account:") or not scope.removeprefix("account:"):
        return False
    name = str(name or "").strip()
    try:
        place = ZoneInfo(name) if name else None
    except (ZoneInfoNotFoundError, ValueError):
        place = None
    with _account_zones_lock:
        if place is None:
            _account_zones.pop(scope, None)
            return False
        _account_zones[scope] = (name, place)
    return True


def _account_zone() -> tuple[str, tzinfo] | None:
    # Agent turns run in long-lived worker tasks: the context set by the inbound
    # messenger task does not propagate through the queue. The existing memory scope
    # is installed by the worker for each turn and is the trusted identity to use.
    from nanomuse.memory.scope import current_memory_scope

    scope = f"account:{runtime_account()}" if runtime_account() else current_memory_scope()
    with _account_zones_lock:
        return _account_zones.get(scope)


def zone_name() -> str:
    """The IANA name in use: the account's when set, else the system's ("" if unplaceable)."""
    if runtime_account() or shared_bot():
        account = _account_zone()
        return account[0] if account is not None else system_zone_name()
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
    if runtime_account() or shared_bot():
        return _account_zone() is not None
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
    if runtime_account() or shared_bot():
        account = _account_zone()
        return account[1] if account is not None else datetime.now().astimezone().tzinfo or UTC
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
    "set_account_zone",
    "shared_bot",
    "trusted",
    "stamp",
    "system_zone_name",
    "today",
    "zone",
    "zone_name",
]
