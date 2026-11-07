"""Books for the voice agent: TXT, EPUB and FB2, read paragraph by paragraph.

A bookmark per book lives in ``state.json`` next to the script, so "продолжай" picks up
where the voice stopped last time.
"""

from __future__ import annotations

import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Awaitable, Callable
from xml.etree import ElementTree

SUFFIXES = {".txt", ".fb2", ".epub", ".fbz"}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in {"script", "style", "head"}:
            self.skip += 1
        elif tag in {"p", "div", "br", "h1", "h2", "h3", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "head"} and self.skip:
            self.skip -= 1
        elif tag in {"p", "div", "h1", "h2", "h3", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def _clean(parts: list[str]) -> list[str]:
    text = " ".join(parts)
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    paragraphs = [line.strip() for line in text.splitlines()]
    return [line for line in paragraphs if len(line) > 1]


def _from_txt(path: Path) -> tuple[str, list[str]]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", "replace")
    lines = [re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in text.splitlines()]
    return path.stem, [line for line in lines if len(line) > 1]


def _from_epub(path: Path) -> tuple[str, list[str]]:
    paragraphs: list[str] = []
    with zipfile.ZipFile(path) as archive:
        names = sorted(
            name
            for name in archive.namelist()
            if name.lower().endswith((".xhtml", ".html", ".htm")) and "toc" not in name.lower()
        )
        for name in names:
            parser = _Text()
            parser.feed(archive.read(name).decode("utf-8", "replace"))
            paragraphs.extend(_clean(parser.parts))
    return path.stem, paragraphs


def _from_fb2(path: Path) -> tuple[str, list[str]]:
    root = ElementTree.parse(path).getroot()
    title = path.stem
    book_title = root.find(".//{*}book-title")
    if book_title is not None and book_title.text:
        title = book_title.text.strip()
    paragraphs: list[str] = []
    for element in root.iter():
        tag = element.tag.split("}")[-1]
        if tag in {"p", "subtitle", "title"} and element.text:
            line = " ".join(element.itertext()).strip()
            if len(line) > 1:
                paragraphs.append(line)
    return title, paragraphs


def _load(path: Path) -> tuple[str, list[str]]:
    suffix = path.suffix.lower()
    if suffix in {".epub", ".fbz"}:
        return _from_epub(path)
    if suffix == ".fb2":
        return _from_fb2(path)
    return _from_txt(path)


_TRANSLIT = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "j", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
        "ы": "y", "ь": "", "э": "e", "ю": "ju", "я": "ja",
    }
)


def _latin_words(text: str) -> list[str]:
    """Words of a title in Latin letters, so «Идиот» finds ``idiot.txt``."""
    latin = text.lower().translate(_TRANSLIT)
    return [word for word in re.split(r"[^a-z0-9]+", latin) if len(word) > 2]


def find_book(folder: Path, wanted: str) -> Path | None:
    if not folder.is_dir():
        return None
    books = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUFFIXES)
    if not books:
        return None
    words = _latin_words(wanted)
    if not words:
        return None
    best: tuple[int, Path] | None = None
    for book in books:
        name = " ".join(_latin_words(book.stem))
        score = sum(1 for word in words if word in name)
        if score and (best is None or score > best[0]):
            best = (score, book)
    return best[1] if best else None


def _chunks(paragraph: str, limit: int = 400) -> list[str]:
    """Split a paragraph into sentence groups of up to ``limit`` characters.

    edge-tts renders one clip per call: one sentence per call means a pause between every
    sentence, a paragraph per call means a long wait before the first word. Groups of a
    few sentences keep the stream smooth and the first word close.
    """
    sentences = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", paragraph) if s.strip()]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + len(sentence) + 1 > limit:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


class Reader:
    def __init__(
        self,
        path: Path,
        state: dict,
        key: str | None = None,
        title: str | None = None,
        on_save: Callable[[], None] | None = None,
    ) -> None:
        self.path = path
        self.state = state
        self.key = key or str(path)
        self.on_save = on_save
        loaded_title, self.paragraphs = _load(path)
        self.title = title or loaded_title
        saved = state.get(self.key) or {}
        self.index = max(0, min(int(saved.get("index", 0)), max(0, len(self.paragraphs) - 1)))

    def where(self) -> str:
        if not self.paragraphs:
            return "В книге не нашлось текста."
        head = self.paragraphs[self.index][:80]
        return f"«{self.title}», абзац {self.index + 1} из {len(self.paragraphs)}: {head}"

    def save(self) -> None:
        self.state[self.key] = {"index": self.index}
        if self.on_save is not None:
            self.on_save()

    async def read_aloud(self, say: Callable[[str], Awaitable[None]]) -> None:
        if not self.paragraphs:
            await say("В книге не нашлось текста.")
            return
        while self.index < len(self.paragraphs):
            paragraph = self.paragraphs[self.index]
            # Long paragraphs are spoken in groups of sentences: one clip per sentence would
            # leave a pause between every sentence, one clip per paragraph would delay the start.
            for piece in _chunks(paragraph):
                if piece:
                    await say(piece)
            self.index += 1
            self.save()
        await say("Книга закончилась.")
