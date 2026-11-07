"""Russian commands for the mixer and the reader, shared by the console player and the agent.

``parse`` turns a spoken or typed phrase into one action: music on or off, a volume step,
a book to read, pause, continue. Anything else is ``chat``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MUSIC_WORDS = ("музык", "трек", "плейлист", "песн")
OFF_WORDS = ("выключ", "убери", "убрать", "останов", "стоп", "хватит", "замолчи", "заглуши")
QUIET_WORDS = ("тише", "потише", "сбавь", "убавь", "тихо")
LOUD_WORDS = ("громче", "погромче", "прибавь", "добавь")
ON_WORDS = ("включ", "вруби", "играй", "давай", "запусти")
READ_PREFIXES = (
    "читай",
    "почитай",
    "включи книгу",
    "открой книгу",
    "включи читалку",
    "открой",
)
PAUSE_WORDS = {"стоп", "пауза", "хватит", "замолчи"}
VOLUME_DOWN = {"тише", "потише", "сбавь", "убавь", "тихо", "потише музыку"}
VOLUME_UP = {"громче", "погромче", "прибавь", "добавь", "громче музыку"}
NEXT_WORDS = {"продолжай", "продолжи", "дальше", "читай дальше", "продолжи чтение"}
STATUS_WORDS = {"что читаешь", "где мы", "закладка", "статус"}


@dataclass(frozen=True)
class Action:
    kind: str
    text: str = ""
    value: float | None = None


def _norm(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split()).strip(" .,!?…")


def parse(text: str) -> Action:
    low = _norm(text)
    if not low:
        return Action("chat", text=text)

    if any(word in low for word in MUSIC_WORDS):
        if any(word in low for word in OFF_WORDS):
            return Action("music_off")
        if any(word in low for word in QUIET_WORDS):
            return Action("music_down")
        if any(word in low for word in LOUD_WORDS):
            return Action("music_up")
        percent = re.search(r"\b(\d{1,3})\b", low)
        if percent is not None:
            return Action("music_volume", value=int(percent.group(1)) / 100)
        if any(word in low for word in ON_WORDS) or low in {"музыка", "музыку"}:
            return Action("music_on")
        return Action("music_on")

    if low in VOLUME_DOWN:
        return Action("music_down")
    if low in VOLUME_UP:
        return Action("music_up")

    if low in PAUSE_WORDS:
        return Action("pause")
    if low in NEXT_WORDS:
        return Action("continue")
    if low in STATUS_WORDS:
        return Action("status")
    if low in {"музыка", "музыку"}:
        return Action("music_on")

    for prefix in READ_PREFIXES:
        if low.startswith(prefix + " ") or low == prefix:
            wanted = low[len(prefix) :].strip()
            if prefix == "открой" and not wanted:
                break
            return Action("read", text=wanted)
    return Action("chat", text=text)


def describe_level(level: float) -> str:
    return f"{round(level * 100)}%"
