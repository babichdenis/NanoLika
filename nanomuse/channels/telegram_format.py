"""A model's Markdown as Telegram HTML, cut into messages the Bot API accepts.

The model writes Markdown; the Bot API shows one chosen parse mode and nothing else. This
module is the single place between the two: ``&``, ``<`` and ``>`` are escaped first, a small
set of Markdown constructs becomes Bot API tags, and the answer is cut into messages at
paragraph, line and word boundaries, so a ``<pre>``, ``<a>``, ``<b>``, ``<i>`` or ``<code>``
never stays open at the end of a message.

Each message comes with its own plain text: when Telegram refuses the markup
(``can't parse entities``), the same words are still delivered instead of being lost.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from html import escape as _attribute
from typing import NamedTuple

# the Bot API refuses a longer message; the reserve leaves room for tag overhead and for a
# cut that lands between two words
LIMIT = 4096
RESERVE = 96
# a long answer is a few messages, not a burst: the rest is one line at the end
MAX_PARTS = 6

MORE = {
    "ru": "…и ещё {n} {word}, открой в приложении",
    "en": "…and {n} more {word}, open the app to read the rest",
    "zh": "……还有 {n} 段，请在应用中查看",
}

_FENCE = re.compile(r"^\s*```")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^(\s*)[-*+•]\s+(.*)$")
_NUMBERED = re.compile(r"^(\s*)(\d{1,3})[.)]\s+(.*)$")
_CODE_SPAN = re.compile(r"(`[^`\n]+`)")
_LINK = re.compile(r"\[([^\]\n]+)\]\(([^()\s]+)\)")
_BOLD_STARS = re.compile(r"\*\*(.+?)\*\*")
_BOLD_UNDER = re.compile(r"__(.+?)__")
_ITALIC_STARS = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)")
_ITALIC_UNDER = re.compile(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])")


class Part(NamedTuple):
    """One message: its HTML, and the same words without markup for a refused parse."""

    html: str
    text: str


def escape(text: str) -> str:
    """The three characters the Bot API's HTML mode reads as markup."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def plain(html: str) -> str:
    """The words of rendered HTML, for the fallback that sends no markup at all."""
    text = re.sub(r"<[^>]*>", "", str(html))
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def render(text: str) -> str:
    """The whole answer as one HTML string, paragraphs separated by a blank line."""
    blocks = [html for html in (_render_block(block) for block in _blocks(text)) if html.strip()]
    return "\n\n".join(blocks)


def parts(text: str, *, lang: str = "", limit: int = LIMIT - RESERVE) -> list[Part]:
    """The answer as messages Telegram accepts, each with complete tags inside it."""
    messages: list[list[list[str]]] = []
    sizes: list[int] = []
    for block in _blocks(text):
        for piece in _fit(block, limit):
            html = _render_block(piece)
            if not html.strip():
                continue
            if sizes and sizes[-1] + 2 + len(html) <= limit:
                messages[-1].append(piece)
                sizes[-1] += 2 + len(html)
            else:
                messages.append([piece])
                sizes.append(len(html))
    out: list[Part] = []
    for message in messages:
        html = "\n\n".join(part for part in (_render_block(b) for b in message) if part.strip())
        if not html.strip():
            continue
        source = "\n\n".join(part for part in (_source(b) for b in message) if part.strip())
        out.append(Part(html=html, text=source))
    if len(out) > MAX_PARTS:
        rest = len(out) - (MAX_PARTS - 1)
        note = _note(lang, rest)
        out = out[: MAX_PARTS - 1] + [Part(html=f"<i>{escape(note)}</i>", text=note)]
    return out


# ---------------------------------------------------------------------- blocks


def _blocks(text: str) -> list[list[str]]:
    """Paragraphs, with a fenced code block kept whole however many blank lines it holds."""
    blocks: list[list[str]] = []
    current: list[str] = []
    fenced = False
    body = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    for line in body.split("\n"):
        fence = bool(_FENCE.match(line))
        if fenced:
            current.append(line)
            if fence:
                blocks.append(current)
                current = []
                fenced = False
            continue
        if fence:
            if current:
                blocks.append(current)
                current = []
            current = [line]
            fenced = True
            continue
        if not line.strip():
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _render_block(block: list[str]) -> str:
    if not block:
        return ""
    if _FENCE.match(block[0]):
        body = list(block[1:])
        if body and _FENCE.match(body[-1]):
            body = body[:-1]
        code = "\n".join(body)
        return f"<pre>{escape(code)}</pre>" if code.strip() else ""
    lines = [html for html in (_render_line(line) for line in block) if html.strip()]
    return "\n".join(lines)


def _source(block: list[str]) -> str:
    """The block without its fence markers, for the message that carries no markup."""
    if not block:
        return ""
    if _FENCE.match(block[0]):
        body = list(block[1:])
        if body and _FENCE.match(body[-1]):
            body = body[:-1]
        return "\n".join(body).strip()
    return "\n".join(block).strip()


def _render_line(line: str) -> str:
    heading = _HEADING.match(line)
    if heading:
        body = heading.group(1).strip()
        return f"<b>{_inline(body)}</b>" if body else ""
    bullet = _BULLET.match(line)
    if bullet:
        return f"{_indent(bullet.group(1))}• {_inline(bullet.group(2).strip())}"
    numbered = _NUMBERED.match(line)
    if numbered:
        indent, number, body = numbered.groups()
        return f"{_indent(indent)}{number}. {_inline(body.strip())}"
    return _inline(line)


def _indent(prefix: str) -> str:
    """One nesting level is two spaces: the client draws the item, the bot only pads it."""
    return "  " * (len(prefix.expandtabs(2)) // 2)


def _inline(text: str) -> str:
    """One line of Markdown as HTML; a code span is never formatted inside."""
    out: list[str] = []
    for index, piece in enumerate(_CODE_SPAN.split(text)):
        if index % 2:
            out.append(f"<code>{escape(piece[1:-1])}</code>")
        else:
            out.append(_prose(piece))
    return "".join(out)


def _prose(text: str) -> str:
    """Bold, italic and links around escaped words; links are held aside so an address
    keeps its underscores and its stars."""
    links: list[str] = []

    def hold(match: re.Match[str]) -> str:
        links.append(
            f'<a href="{_attribute(match.group(2), quote=True)}">{_prose(match.group(1))}</a>'
        )
        return f"\x00{len(links) - 1}\x00"

    held = _LINK.sub(hold, text)
    escaped = escape(held)
    escaped = _BOLD_STARS.sub(r"<b>\1</b>", escaped)
    escaped = _BOLD_UNDER.sub(r"<b>\1</b>", escaped)
    escaped = _ITALIC_STARS.sub(r"<i>\1</i>", escaped)
    escaped = _ITALIC_UNDER.sub(r"<i>\1</i>", escaped)
    return re.sub(r"\x00(\d+)\x00", lambda m: links[int(m.group(1))], escaped)


# ---------------------------------------------------------------------- cutting


def _fit(block: list[str], limit: int) -> list[list[str]]:
    """The block, or the few blocks it becomes, each rendering inside ``limit``."""
    if len(_render_block(block)) <= limit:
        return [block]
    if _FENCE.match(block[0]):
        language = block[0].strip().lstrip("`").strip()
        body = list(block[1:])
        if body and _FENCE.match(body[-1]):
            body = body[:-1]
        room = max(256, limit - len("<pre></pre>"))
        groups = _group(body, room, lambda line: len(escape(line)) + 1)
        return [
            [f"```{language}", *group, "```"]
            for group in groups
            if any(line.strip() for line in group)
        ]
    pieces: list[list[str]] = []
    for line in block:
        if len(_render_line(line)) <= limit:
            pieces.append([line])
            continue
        pieces.extend([[chunk] for chunk in _cut(line, limit)])
    return pieces


def _group(
    lines: list[str], room: int, measure: Callable[[str], int] | None = None
) -> list[list[str]]:
    """Lines packed into groups whose measured length stays inside ``room``."""
    size: Callable[[str], int] = measure or (lambda line: len(line) + 1)
    groups: list[list[str]] = []
    current: list[str] = []
    used = 0
    for line in lines:
        length = size(line)
        if current and used + length > room:
            groups.append(current)
            current = []
            used = 0
        current.append(line)
        used += length
    if current:
        groups.append(current)
    return groups


def _cut(line: str, limit: int) -> list[str]:
    """A line too long for one message: by words, and a single word by its letters."""
    words = re.findall(r"\S+\s*", line)
    out: list[str] = []
    current = ""
    for word in words:
        candidate = current + word
        if current and len(_render_line(candidate.rstrip())) > limit:
            out.append(current.rstrip())
            current = word
        else:
            current = candidate
    if current.strip():
        out.append(current.rstrip())
    chunks: list[str] = []
    for piece in out:
        while len(_render_line(piece)) > limit and len(piece) > 1:
            step = max(1, min(len(piece) - 1, limit // 2))
            while step > 1 and len(_render_line(piece[:step])) > limit:
                step //= 2
            chunks.append(piece[:step])
            piece = piece[step:]
        if piece.strip():
            chunks.append(piece)
    return chunks


def _note(lang: str, count: int) -> str:
    code = (lang or "en").strip().lower()
    if code.startswith("ru"):
        return MORE["ru"].format(n=count, word=_russian(count, "часть", "части", "частей"))
    if code.startswith("zh"):
        return MORE["zh"].format(n=count)
    return MORE["en"].format(n=count, word="part" if count == 1 else "parts")


def _russian(count: int, one: str, few: str, many: str) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return one
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return few
    return many


__all__ = ["LIMIT", "MAX_PARTS", "Part", "escape", "parts", "plain", "render"]
