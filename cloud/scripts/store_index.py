#!/usr/bin/env python3
"""Пересчитать каталог склада: ``store/index.json``.

Склад — это каталог на сервере (``/opt/nanomuse/cloud/store``), из которого клиентские
приложения берут то, что не лежит в установщике: навыки, капсулы кода, описания MCP-серверов
(docs/store.md). Индекс — единственное, что читает рантайм; всё остальное он качает по ссылкам
отсюда.

    python3 cloud/scripts/store_index.py                 # store/ рядом с репозиторием
    python3 cloud/scripts/store_index.py --root store     # явный корень
    python3 cloud/scripts/store_index.py --check          # только сказать, устарел ли индекс

Что делает:

* обходит ``skills/*/SKILL.md``, ``mcp/*.json``, ``capsules/*/*/*.zip``;
* считает sha256 и размер каждого файла капсулы;
* пишет ``index.json``: версия склада, дата, и по каждому пакету — раздел, имя, версия,
  описание, платформы, вес, что закрывает (``provides``), куда кладётся (``install``);
* описание берётся из самого пакета: у навыка — front matter SKILL.md, у MCP — поле
  ``description`` в json, у капсулы — ``capsule.json`` внутри zip.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORE = ROOT / "cloud" / "store"
PUBLIC_BASE = "https://cloud.nanolika.ru/store"
PLATFORMS = re.compile(r"^(?P<platform>[a-z0-9]+-[a-z0-9]+)\.zip$")
FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def front_matter(text: str) -> dict[str, str]:
    """Плоские пары ``ключ: значение`` из front matter SKILL.md (вложенное не нужно)."""
    match = FRONT.match(text)
    if not match:
        return {}
    out: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line or line.startswith((" ", "\t", "#")):
            continue
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def skill_entry(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    meta = front_matter(text)
    name = path.parent.name
    return {
        "section": "skills",
        "name": name,
        "description": meta.get("description", ""),
        "url": f"{PUBLIC_BASE}/skills/{name}/SKILL.md",
        "bytes": path.stat().st_size,
        "install": "skill",
    }


def mcp_entry(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    name = data.get("name") or path.stem
    return {
        "section": "mcp",
        "name": name,
        "description": data.get("description", ""),
        "url": f"{PUBLIC_BASE}/mcp/{path.name}",
        "bytes": path.stat().st_size,
        # описание сервера целиком: рантайм заводит его из этого же json
        "server": {
            k: v
            for k, v in data.items()
            if k in ("name", "command", "args", "url", "risk", "egress", "reads_private_data")
        },
        "install": "mcp",
    }


def capsule_files(folder: Path) -> list[dict[str, Any]]:
    """zip-файлы одной версии капсулы: ``capsules/<name>/<version>/<platform>.zip``."""
    files = []
    name, version = folder.parent.name, folder.name
    for path in sorted(folder.glob("*.zip")):
        match = PLATFORMS.match(path.name)
        if not match:
            continue
        files.append(
            {
                "platform": match.group("platform"),
                "url": f"{PUBLIC_BASE}/capsules/{name}/{version}/{path.name}",
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    return files


def capsule_meta(folder: Path) -> dict[str, Any]:
    """Что капсула обещает: её ``capsule.json`` из любого zip этой версии."""
    for path in sorted(folder.glob("*.zip")):
        try:
            with zipfile.ZipFile(path) as zf:
                raw = zf.read("capsule.json")
        except (KeyError, zipfile.BadZipFile):
            continue
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            continue
    return {}


def capsule_entry(folder: Path) -> dict[str, Any] | None:
    files = capsule_files(folder)
    if not files:
        return None
    meta = capsule_meta(folder)
    name = folder.parent.name
    version = folder.name
    return {
        "section": "capsules",
        "name": name,
        "version": version,
        "description": meta.get("description", ""),
        "provides": meta.get("provides", []),
        # какие python-модули капсула даёт: рантайм по этому списку решает, чего не хватает
        "modules": meta.get("modules", []),
        "files": files,
        "bytes": sum(f["bytes"] for f in files),
        "install": "addon",
    }


def build(root: Path) -> dict[str, Any]:
    packages: list[dict[str, Any]] = []
    problems: list[str] = []

    skills = root / "skills"
    if skills.is_dir():
        for path in sorted(skills.glob("*/SKILL.md")):
            entry = skill_entry(path)
            if not entry["description"]:
                problems.append(f"{path}: нет description во front matter")
            packages.append(entry)

    mcp = root / "mcp"
    if mcp.is_dir():
        for path in sorted(mcp.glob("*.json")):
            try:
                packages.append(mcp_entry(path))
            except (ValueError, OSError) as exc:
                problems.append(f"{path}: {exc}")

    capsules = root / "capsules"
    if capsules.is_dir():
        for name_dir in sorted(p for p in capsules.iterdir() if p.is_dir()):
            versions = [p for p in name_dir.iterdir() if p.is_dir()]
            # последняя версия по имени: 1.2.0 > 1.10.0 не надо, складу хватает одной
            for folder in sorted(versions):
                entry = capsule_entry(folder)
                if entry is None:
                    problems.append(f"{folder}: нет ни одного <platform>.zip")
                    continue
                if not entry["description"]:
                    problems.append(f"{folder}: capsule.json без description")
                packages.append(entry)

    index = {
        "version": 1,
        "updated": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "base": PUBLIC_BASE,
        "packages": packages,
        "problems": problems,
    }
    return index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="каталог склада NanoLika")
    parser.add_argument("--root", default=str(DEFAULT_STORE), help="корень склада")
    parser.add_argument("--check", action="store_true", help="не писать, только сравнить")
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"нет каталога склада: {root}", file=sys.stderr)
        return 1

    index = build(root)
    target = root / "index.json"
    text = json.dumps(index, ensure_ascii=False, indent=2) + "\n"

    if args.check:
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        fresh = {k: v for k, v in index.items() if k != "updated"}
        try:
            existing = {k: v for k, v in json.loads(current or "{}").items() if k != "updated"}
        except ValueError:
            existing = None
        if existing != fresh:
            print("index.json устарел: запустить cloud/scripts/store_index.py", file=sys.stderr)
            return 1
        print("index.json в порядке")
        return 0

    target.write_text(text, encoding="utf-8")
    total = sum(p["bytes"] for p in index["packages"] if p["bytes"])
    print(f"склад: {len(index['packages'])} пакетов, {total / 1048576:.1f} МБ")
    for package in index["packages"]:
        where = package.get("version") and f" {package['version']}" or ""
        platforms = ""
        if package["section"] == "capsules":
            platforms = " [" + ", ".join(f["platform"] for f in package["files"]) + "]"
        print(f"  {package['section']}/{package['name']}{where}{platforms}")
    for problem in index["problems"]:
        print(f"  ! {problem}", file=sys.stderr)
    print(f"записан {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
