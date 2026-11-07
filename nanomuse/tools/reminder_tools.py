"""Reminders and routines (create / list / cancel)."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from nanomuse import timeutil
from nanomuse.reminders import ReminderStore
from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment

DURATION = re.compile(r"\s*(\d+(?:\.\d+)?)\s*([dhms])", re.IGNORECASE)
_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> timedelta | None:
    """`5m`, `90s`, `1h30m`, `2 d` → a timedelta; ``None`` when it is not a duration.

    Durations are the tool's job, not the model's: an hour-floored clock in the prompt
    makes "in five minutes" wrong by up to 59 minutes if the model does the arithmetic."""
    body = (text or "").strip().lower()
    if not body:
        return None
    parts = DURATION.findall(body)
    if not parts or DURATION.sub("", body).strip(" +,"):
        return None
    seconds = sum(float(number) * _UNIT[unit.lower()] for number, unit in parts)
    if seconds <= 0:
        return None
    return timedelta(seconds=seconds)


class Reminders(BaseTool):
    name: str = "reminders"
    description: str = (
        "Schedule something for later. `remind` tells the user the text at that time; `task` "
        "does the work and reports the result. Actions: `create` (text, kind, and `in` for a "
        "duration or `at` for a one-off or `repeat` for a routine), `list`, `cancel` "
        "(reminder_id). For 'in five minutes', 'через час' pass `in` ('5m', '1h') and the tool "
        "does the arithmetic; use `at` only for a clock time the person named. It fires "
        "whether or not the app is open."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "list", "cancel"]},
            "text": {
                "type": "string",
                "description": "What to remind about, or what to do, in the user's words.",
            },
            "kind": {"type": "string", "enum": ["remind", "task"]},
            "in": {
                "type": "string",
                "description": (
                    "One-off, from now: '5m', '90s', '2h', '1h30m'. Use it for a duration the "
                    "person asked for; the tool adds it to the clock itself."
                ),
            },
            "at": {
                "type": "string",
                "description": "One-off at a clock time: 'YYYY-MM-DD HH:MM' in the user's local time.",
            },
            "repeat": {
                "type": "string",
                "description": (
                    "Routine: 'daily 08:00', 'weekdays 07:30', 'weekly mon 09:00', "
                    "'monthly 1 09:00' (local time)."
                ),
            },
            "reminder_id": {"type": "string"},
        },
        "required": ["action"],
    }
    risk: RiskLevel = RiskLevel.SAFE
    store: ReminderStore
    # which chat a new item belongs to (the server sets this; the CLI has one chat)
    thread_of: Callable[[], str] = lambda: "main"
    # the server hooks this to reschedule its loop when something new is due soon
    on_change: Callable[[], None] | None = None

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        a = super().assess(args)
        action = str(args.get("action") or "?")
        if action == "create":
            # a reminder, a routine above all, is work the model does later on its own:
            # creating one changes state (strict mode asks); listing and cancelling stay safe
            a.risk = RiskLevel.MODERATE
            when = args.get("repeat") or args.get("at") or "?"
            detail = f"{when}: {str(args.get('text') or '')[:60]}"
        elif action == "cancel":
            detail = str(args.get("reminder_id") or "")
        else:
            detail = ""
        a.summary = f"reminders {action}" + (f": {detail}" if detail else "")
        return a

    async def execute(
        self,
        action: str = "",
        text: str | None = None,
        kind: str | None = None,
        at: str | None = None,
        repeat: str | None = None,
        reminder_id: str | None = None,
        **_: Any,
    ) -> ToolResult:
        # `in` is a Python keyword, so the schema's property arrives in **_ and is read here
        duration = str((_ or {}).get("in") or "").strip()
        try:
            if action == "create":
                if not text:
                    return ToolResult.fail("`text` is required")
                if duration and at:
                    return ToolResult.fail(
                        "give `in` (a duration) or `at` (a clock time), not both"
                    )
                if duration:
                    delta = parse_duration(duration)
                    if delta is None:
                        return ToolResult.fail("`in` is a duration like '5m', '90s' or '1h30m'")
                    # on the person's clock: the tool adds the delta, the model does not
                    at = (timeutil.now() + delta).strftime("%Y-%m-%d %H:%M")
                if not (at or repeat):
                    return ToolResult.fail("give `in` or `at` (one-off) or `repeat` (routine)")
                item = self.store.create(
                    text,
                    at=at or "",
                    repeat=repeat or "",
                    kind=kind or "remind",
                    thread=self.thread_of(),
                )
                self._changed()
                verb = "Routine set" if item.repeating else "Reminder set"
                return ToolResult(output=f"{verb}.\n{item.render()}")
            if action == "list":
                items = self.store.list("active")
                if not items:
                    return ToolResult(output="Nothing scheduled.")
                return ToolResult(output="\n".join(i.render() for i in items))
            if action == "cancel":
                if not reminder_id:
                    return ToolResult.fail("`reminder_id` is required")
                cancelled = self.store.cancel(reminder_id)
                if cancelled is None:
                    return ToolResult.fail(f"no active reminder {reminder_id}")
                self._changed()
                return ToolResult(output=f"Cancelled.\n{cancelled.render()}")
            return ToolResult.fail(f"unknown action {action!r}")
        except ValueError as exc:
            return ToolResult.fail(str(exc))

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change()
