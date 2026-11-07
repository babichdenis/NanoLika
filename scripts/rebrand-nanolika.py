#!/usr/bin/env python3
"""Turn the product's old name into NanoLika in text, and only in text.

The package, the imports and the environment keep `nanomuse` (lower case): renaming those
breaks every module path. What this script changes is the *brand* — the standalone words
`NanoLika` and `NanoLika` in docs, comments, UI strings and copy:

    NanoLika  ->  nanoLika
    NanoLika  ->  NanoLika

A word boundary keeps identifiers intact: `NanoMuseCloud`, `nanoMuseHomeAction` and
`NANOMUSE_KEY` are not matched. Repository and domain paths are skipped on purpose
(`github.com/nano-muse/nanoMuse`, `nanoMuse.cn`), because a rename there would break a link.

    python scripts/rebrand-nanolika.py --check   # report only
    python scripts/rebrand-nanolika.py           # apply
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "build", "dist", "__pycache__", ".mypy_cache"}
TEXT_SUFFIXES = {
    ".md", ".markdown", ".txt", ".rst", ".toc",
    ".py", ".kt", ".kts", ".swift", ".java", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".html", ".htm", ".css", ".scss", ".json", ".jsonc", ".yml", ".yaml", ".toml", ".ini",
    ".xml", ".strings", ".xcstrings", ".plist", ".sh", ".sql", ".gradle", ".pro",
}
# A line that names a repository or a host keeps its spelling.
KEEP = re.compile(r"github\.com/[^\s]*nanoMuse|nanoMuse\.(?:cn|ru|cloud)|cloud\.nanoMuse|file:///|[/\\]nanoMuse\b|\bnanoMuse/", re.IGNORECASE)
BRAND = re.compile(r"\b(NanoLika|NanoLika)\b")

changed_files: list[tuple[Path, int]] = []
total = 0


def files() -> list[Path]:
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if any(part in SKIP_DIRS or part.startswith(".venv") for part in path.parts):
            continue
        yield path


def rewrite(text: str) -> tuple[str, int]:
    hits = 0
    out_lines = []
    for line in text.splitlines(keepends=True):
        if KEEP.search(line):
            out_lines.append(line)
            continue
        new, count = BRAND.subn("NanoLika", line)
        hits += count
        out_lines.append(new)
    return "".join(out_lines), hits


def main() -> int:
    check = "--check" in sys.argv
    global total
    for path in files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        new, hits = rewrite(text)
        if not hits:
            continue
        total += hits
        changed_files.append((path.relative_to(ROOT), hits))
        if not check:
            path.write_text(new, encoding="utf-8")

    for path, hits in sorted(changed_files, key=lambda row: -row[1]):
        print(f"{hits:5}  {path}")
    print(f"\n{total} mentions in {len(changed_files)} files" + (" (dry run)" if check else " — rewritten"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
