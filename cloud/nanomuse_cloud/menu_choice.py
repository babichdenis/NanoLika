"""Что из рынка показывать человеку.

На рынке 1 472 позиции у трёх компаний, и большая часть — не то, что человеку нужно
в чате: эмбеддинги, распознавание речи, модели для картинок без текста, позапрошлые
версии, экспериментальные ветки. Цель не «спрятать море», а убрать из него мусор:
человек должен видеть широкий, но осмысленный выбор.

Модуль отвечает на два вопроса и ничего не пишет в базу:

* `clean(models)` — какие позиции достойны клиентского меню (и почему остальные нет);
* `shelves(models)` — на какие полки они ложатся (переписка, кодинг, картинки…).

Правила отсева — явные и проверяемые, каждое с причиной в `verdict`:

    junk:embedding     — вектора, а не разговор
    junk:audio-tool    — распознавание/озвучка вместо чата
    junk:image-tool    — только генерация картинок
    junk:moderation    — модерация и классификация
    junk:deprecated    — помечено как устаревшее у продавца
    junk:duplicate     — тот же поставщик и модель, но дороже соседней строки
    junk:no-price      — цены нет вовсе
    junk:stale         — версия старше трёх поколений (gpt-3.5, claude-2 …)

Отсев намеренно осторожный: строка уходит только если причина неоспорима. Всё
спорное остаётся — лучше лишняя модель, чем выброшенная нужная.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

EMBED = ("embed", "embedding", "bge-", "e5-", "gte-", "nomic-embed", "jina-embeddings")
AUDIO_TOOL = ("whisper", "transcribe", "tts-", "-tts", "speech", "voice-clone", "parakeet", "seamless")
IMAGE_TOOL = ("dall-e", "stable-diffusion", "sdxl", "flux-", "imagen", "ideogram", "recraft", "kolors", "midjourney")
MODERATION = ("moderation", "guard", "nsfw", "classifier", "rerank", "ranker")
DEPRECATED = ("deprecated", "legacy", "-preview-2023", "-0613", "-0314")
STALE = (
    "gpt-3.5",
    "gpt-4-0613",
    "gpt-4-32k",
    "claude-1",
    "claude-2",
    "claude-instant",
    "llama-2",
    "mistral-7b-instruct-v0.1",
    "gemini-pro-vision",
    "text-davinci",
    "command-nightly",
)
CODING = (
    "code",
    "coder",
    "codex",
    "codestral",
    "devstral",
    "kimi-k2",
    "sonnet",
    "opus",
    "swe",
    "deepseek-coder",
    "qwen2.5-coder",
    "qwen3-coder",
)

SHELF_ORDER = ("coding", "chat", "reasoning", "image", "video", "speech", "transcribe", "music")


@dataclass
class Verdict:
    """Что решили про строку и почему."""

    name: str
    keep: bool
    reason: str = ""
    shelves: list[str] = field(default_factory=list)


def _has(text: str, words: tuple[str, ...]) -> str:
    for word in words:
        if word in text:
            return word
    return ""


def shelves_of(name: str, kinds: tuple[str, ...] = ("chat",)) -> list[str]:
    """Полки, на которые ложится модель: по её природе, а не по названию продавца."""
    low = name.lower()
    found: list[str] = []
    if "image" in kinds and not _has(low, IMAGE_TOOL):
        found.append("image")
    if "video" in kinds:
        found.append("video")
    if "speech" in kinds:
        found.append("speech")
    if "transcribe" in kinds or _has(low, AUDIO_TOOL):
        found.append("transcribe")
    if "music" in kinds:
        found.append("music")
    if "chat" in kinds and not found:
        if _has(low, CODING):
            found.append("coding")
        if any(word in low for word in ("reason", "o1", "o3", "o4", "r1", "thinking", "think")):
            found.append("reasoning")
        found.append("chat")
    return found


def verdict(name: str, kinds: tuple[str, ...], cheapest: float | None, deprecated: bool = False) -> Verdict:
    """Стоит ли эта строка клиентского меню — и если нет, то почему именно."""
    low = name.lower()
    found = shelves_of(name, kinds)
    if _has(low, DEPRECATED) or deprecated:
        return Verdict(name, False, "junk:deprecated")
    if _has(low, EMBED):
        return Verdict(name, False, "junk:embedding")
    if _has(low, MODERATION):
        return Verdict(name, False, "junk:moderation")
    if _has(low, STALE):
        return Verdict(name, False, "junk:stale")
    if low.endswith(":free"):
        return Verdict(name, False, "junk:free")
    if not found:
        if _has(low, AUDIO_TOOL):
            return Verdict(name, False, "junk:audio-tool")
        if _has(low, IMAGE_TOOL):
            return Verdict(name, False, "junk:image-tool")
        return Verdict(name, False, "junk:no-kind")
    if cheapest in (None, 0):
        return Verdict(name, False, "junk:no-price")
    return Verdict(name, True, "", found)


def clean(rows: list[dict]) -> tuple[list[Verdict], dict[str, int]]:
    """Разбирает строки таблицы цен: что оставить, что выкинуть и по какой причине.

    `rows` — элементы `prices.json` (`{"name", "group"|"unit", "cheapest_rub"}`).
    Возвращает вердикты и счётчик причин, чтобы отсев можно было проверить глазами.
    """
    kept: list[Verdict] = []
    reasons: dict[str, int] = {}
    cheapest_by_name: dict[str, float] = {}
    for row in rows:
        name = str(row.get("name") or "")
        if not name:
            continue
        rub = row.get("cheapest_rub")
        price = float(rub) if rub not in (None, "") else None
        if name not in cheapest_by_name or (price is not None and price < cheapest_by_name[name]):
            cheapest_by_name[name] = price if price is not None else cheapest_by_name.get(name, 0.0)

    seen: dict[str, float] = {}
    for row in rows:
        name = str(row.get("name") or "")
        if not name:
            continue
        unit = str(row.get("unit") or row.get("group") or "")
        kinds = kind_of(unit)
        price = cheapest_by_name.get(name)
        result = verdict(name, kinds, price, bool(row.get("deprecated")))
        # дубль: то же имя и та же природа, но цена выше — вторая строка ничего не добавляет
        if result.keep:
            key = f"{name}|{','.join(result.shelves)}"
            if key in seen and price is not None and price >= seen[key]:
                result = Verdict(name, False, "junk:duplicate")
            elif price is not None:
                seen[key] = price
        if result.keep:
            kept.append(result)
        else:
            reasons[result.reason] = reasons.get(result.reason, 0) + 1
    return kept, reasons


def kind_of(unit: str) -> tuple[str, ...]:
    """Единица цены говорит, что это за модель: токены — чат, штука — картинка."""
    low = unit.lower()
    if "токен" in low or "token" in low:
        return ("chat",)
    if "картин" in low or "image" in low or "изображ" in low:
        return ("image",)
    if "видео" in low or "клип" in low or "video" in low or "clip" in low:
        return ("video",)
    if "секунд" in low or "second" in low:
        return ("speech",)
    if "символ" in low or "character" in low or "озвуч" in low:
        return ("speech",)
    if "минут" in low or "minute" in low or "трек" in low or "track" in low:
        return ("transcribe",)
    return ("chat",)


def slug(name: str) -> str:
    """Идентификатор модели в меню: ровно то, что пойдёт в приложение."""
    base = name.split("/")[-1] if "/" in name else name
    out = re.sub(r"[^a-z0-9.]+", "-", base.lower()).strip("-")
    return out or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def unit_key(unit: str) -> str:
    """Единица цены без слов продавца: чтобы «вход» и «выход» не считались разными штуками.

    Одна модель часто стоит двумя строками (вход, выход) и разными словами («1M токенов»,
    «токен»). Сравнивать цены можно только внутри одной единицы, иначе получается ложь:
    whisper за секунду против whisper за минуту выглядит в 60 раз дороже.
    """
    low = unit.lower()
    if "токен" in low or "token" in low:
        return "token"
    if "символ" in low or "character" in low:
        return "character"
    if "секунд" in low or "second" in low:
        return "second"
    if "минут" in low or "minute" in low:
        return "minute"
    if "картин" in low or "изображ" in low or "image" in low or "picture" in low:
        return "image"
    if "видео" in low or "клип" in low or "video" in low or "clip" in low:
        return "clip"
    if "трек" in low or "track" in low:
        return "track"
    return "other"


UNIT_NAMES = {
    "token": "1M токенов",
    "character": "1000 символов",
    "second": "секунда",
    "minute": "минута",
    "image": "картинка",
    "clip": "клип",
    "track": "трек",
    "other": "единица",
}
