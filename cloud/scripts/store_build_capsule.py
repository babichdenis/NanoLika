#!/usr/bin/env python3
"""Собрать капсулу склада: разложить рядом с кодом то, что она обещает.

Капсула в git — это только её собственный код (``ocr_vision.py``, ``capsule.json``) и
зависимости, перечисленные в ``capsule.json`` полем ``vendored`` (имя пакета на PyPI и версия).
Скрипт скачивает их колесом под нужную платформу, распаковывает без зависимостей, убирает
лишнее (тесты, метаданные), упаковывает zip и пересчитывает каталог склада.

    python3 cloud/scripts/store_build_capsule.py ocr-vision
    python3 cloud/scripts/store_build_capsule.py ocr-vision --platform darwin-arm64
    python3 cloud/scripts/store_build_capsule.py --all

Платформа по умолчанию — эта машина: капсулы собираются там, где их проверяют, и уезжают на
сервер вместе с ``index.json`` (docs/store.md).
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / "cloud" / "store"
CAPSULES = STORE / "capsules"
#: что не нужно на машине клиента: тесты и служебные каталоги
DROP_DIRS = ("PyObjCTest", "tests", "test", "__pycache__")
DROP_SUFFIXES = (".pyc", ".pyi", ".dist-info")


def host_platform() -> str:
    machine = {"x86_64": "x64", "AMD64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(
        platform.machine(), "x64"
    )
    return f"{sys.platform}-{machine}"


def pypi_target(platform_tag: str) -> list[str]:
    """Аргументы pip для чужой платформы (собирать на своей — просто без них)."""
    system, _, arch = platform_tag.partition("-")
    if system == sys.platform and arch == host_platform().split("-")[1]:
        return []
    return ["--platform", f"{system}_{arch}", "--only-binary", ":all:", "--python-version", "3.11"]


def vendor(folder: Path, packages: list[dict[str, str]], platform_tag: str) -> None:
    """Скачать и распаковать перечисленные пакеты в папку капсулы."""
    if not packages:
        return
    with tempfile.TemporaryDirectory() as tmp:
        for package in packages:
            spec = f"{package['name']}=={package['version']}"
            cmd = [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--quiet",
                "--no-deps",
                "--no-compile",
                "--target",
                tmp,
                *pypi_target(platform_tag),
                spec,
            ]
            print("+", " ".join(cmd))
            subprocess.run(cmd, check=True)
        for entry in Path(tmp).iterdir():
            target = folder / entry.name
            if target.exists():
                shutil.rmtree(target) if target.is_dir() else target.unlink()
            shutil.move(str(entry), str(target))


def prune(folder: Path) -> None:
    """Убрать то, что на машине клиента только занимает место."""
    for path in list(folder.rglob("*")):
        if path.is_dir() and path.name in DROP_DIRS:
            shutil.rmtree(path, ignore_errors=True)
        elif path.is_file() and (
            path.suffix in (".pyc", ".pyi") or path.name.endswith(DROP_SUFFIXES)
        ):
            path.unlink(missing_ok=True)


def build(name: str, platform_tag: str, *, keep_zip_only: bool = True) -> Path:
    source = next((p for p in sorted((CAPSULES / name).iterdir()) if p.is_dir()), None)
    if source is None:
        raise SystemExit(f"нет капсулы {name} в {CAPSULES}")
    meta = json.loads((source / "capsule.json").read_text(encoding="utf-8"))
    if platform_tag not in (meta.get("platforms") or [platform_tag]):
        raise SystemExit(f"{name} не собирается под {platform_tag} (только {meta.get('platforms')})")

    vendor(source, meta.get("vendored") or [], platform_tag)
    prune(source)

    archive = source / f"{platform_tag}.zip"
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source.rglob("*")):
            if path.is_dir() or path.name == archive.name or path.name.endswith(".zip"):
                continue
            zf.write(path, path.relative_to(source))
    size = archive.stat().st_size
    print(f"{name} {platform_tag}: {size / 1048576:.1f} МБ → {archive}")
    if keep_zip_only:
        # после сборки в дереве остаются только код капсулы и её capsule.json
        for path in list(source.iterdir()):
            if path.name in ("capsule.json", "README.md") or path.suffix == ".py":
                continue
            if path.is_dir() or path.suffix == ".zip":
                if path.suffix == ".zip":
                    continue
                shutil.rmtree(path, ignore_errors=True)
    return archive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="сборка капсул склада NanoLika")
    parser.add_argument("names", nargs="*", help="имена капсул; пусто с --all — все")
    parser.add_argument("--all", action="store_true", help="собрать все капсулы")
    parser.add_argument("--platform", default=host_platform(), help="например darwin-arm64")
    parser.add_argument("--keep", action="store_true", help="оставить распакованные зависимости")
    args = parser.parse_args(argv)

    names = args.names
    if args.all or not names:
        names = [p.name for p in sorted(CAPSULES.iterdir()) if p.is_dir()]
    if not names:
        print("нечего собирать", file=sys.stderr)
        return 1
    for name in names:
        build(name, args.platform, keep_zip_only=not args.keep)
    index = ROOT / "cloud" / "scripts" / "store_index.py"
    return subprocess.run([sys.executable, str(index)]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
