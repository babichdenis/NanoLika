"""The runtime's words follow the voice rule in AGENTS.md: a sentence a person or the model
reads carries no em dash. This looks at every string literal under ``nanomuse/`` (docstrings
and comments are for contributors and are not checked), the built-in skills, the provider
catalogue and the example configuration, so a dash does not come back unnoticed."""

from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "nanomuse"
DASH = "—"  # the Chinese "——" is two of them

# character sets that strip or match a dash the model may have written: not sentences
ALLOWED = {
    ("nanomuse/computer/screen.py", r'r"\s+[-—–]\s+([^-—–]+)$"'),
    ("nanomuse/prompts.py", '" :,-—\\n"'),
    # the separator and the edge characters a title is trimmed with: a dash there is data,
    # what a model may have written before the date it puts into a name
    ("nanomuse/channels/telegram_topics.py", r'r"(?:[·•|,;:—–\-(\[]\s*)"'),
    ("nanomuse/channels/telegram_topics.py", r'" \t·•|,;:—–-()[]"'),
}


def _docstring_lines(tree: ast.AST) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return lines


def _string_tokens(path: Path) -> list[tuple[int, str]]:
    source = path.read_text(encoding="utf-8")
    skip = _docstring_lines(ast.parse(source))
    kinds = {tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", -1)}
    return [
        (tok.start[0], tok.string)
        for tok in tokenize.generate_tokens(io.StringIO(source).readline)
        if tok.type in kinds and tok.start[0] not in skip
    ]


def test_no_em_dash_in_the_runtime_s_strings():
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "static" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        for line, text in _string_tokens(path):
            if DASH in text and (rel, text.strip()) not in ALLOWED:
                found.append(f"{rel}:{line}: {text.strip()[:80]}")
    assert not found, "em dashes in strings a person or the model reads:\n" + "\n".join(found)


def test_no_em_dash_in_skills_catalogue_and_example_config():
    files = [
        *sorted(PACKAGE.glob("skills/builtin/*/SKILL.md")),
        PACKAGE / "llm" / "providers.json",
        ROOT / "config" / "config.example.toml",
    ]
    found = []
    for path in files:
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if DASH in line:
                found.append(f"{path.relative_to(ROOT).as_posix()}:{no}: {line.strip()[:80]}")
    assert not found, "em dashes:\n" + "\n".join(found)
