#!/usr/bin/env python3
"""Обновление NanoLika.app для macOS из зеркала обновлений.

Источник — манифест `latest.json` на зеркале (по умолчанию
https://cloud.nanolika.ru/dl/mac/latest.json):

    {"version": "1.0.6",
     "released": "2026-10-23T12:00:00Z",
     "notes": "что нового, одной строкой",
     "assets": {"x64":   {"url": "NanoLika-Desktop-1.0.6-mac-x64.zip",   "sha256": "…", "size": 12345},
                "arm64": {"url": "NanoLika-Desktop-1.0.6-mac-arm64.zip", "sha256": "…", "size": 12345}}}

    update-lika              # проверить и обновить, если есть новее
    update-lika --check      # только сказать, есть ли новее
    update-lika --dry-run    # скачать и проверить, приложение не трогать
    update-lika --force      # поставить, даже если версия та же

Что делает: сверяет версию с Info.plist, качает zip для своей архитектуры,
сверяет SHA-256, закрывает приложение, кладёт старую копию в Корзину,
распаковывает новую в /Applications и запускает.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import platform
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

DEFAULT_APP = Path("/Applications/NanoLika.app")
DEFAULT_MANIFEST = "https://cloud.nanolika.ru/dl/mac/latest.json"
BUNDLE_ID = "io.github.nanomuse.desktop"


def tls_context() -> ssl.SSLContext:
    """Проверяющий контекст, который работает и на питоне без системных сертификатов."""
    try:
        import certifi  # noqa: PLC0415

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    for candidate in ("/etc/ssl/cert.pem", "/usr/local/etc/openssl/cert.pem"):
        if Path(candidate).is_file():
            return ssl.create_default_context(cafile=candidate)
    return ssl.create_default_context()


def version_tuple(text: str) -> tuple:
    parts = []
    for chunk in str(text).split("-")[0].split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def installed_version(app: Path) -> str:
    with (app / "Contents" / "Info.plist").open("rb") as fh:
        return plistlib.load(fh).get("CFBundleShortVersionString", "0")


def fetch_manifest(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "update-lika/1"})
    last: Exception | None = None
    for attempt in (opener(), proxied_opener()):
        try:
            with attempt.open(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise SystemExit(f"на зеркале ещё нет манифеста ({url}) — обновляться не с чего")
            last = exc
        except Exception as exc:  # noqa: BLE001 - вторая попытка решает, что показать
            last = exc
    raise SystemExit(f"зеркало недоступно ({url}): {last}")


def asset_for(manifest: dict) -> tuple[str, dict]:
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    assets = manifest.get("assets") or {}
    if arch not in assets:
        raise SystemExit(f"в манифесте нет сборки для {arch} (есть: {', '.join(assets) or '—'})")
    return arch, assets[arch]


def opener():
    """Прямое соединение сначала, системный прокси — вторым.

    На этом Mac в системе прописан HTTP-прокси 127.0.0.1:10810 (xray), и urllib берёт его
    через _scproxy: запрос к нашему зеркалу через него висит до таймаута, хотя curl напрямую
    отвечает за секунду. Поэтому сначала без прокси, а если сеть доступна только через него —
    повторяем с прокси из системы.
    """
    handlers = [urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=tls_context())]
    return urllib.request.build_opener(*handlers)


def proxied_opener():
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=tls_context()))


def download(url: str, dest: Path) -> str:
    digest = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": "update-lika/1"})
    attempts = (opener(), proxied_opener())
    last = None
    for attempt in attempts:
        try:
            resp = attempt.open(req, timeout=600)
            break
        except Exception as exc:  # noqa: BLE001 - вторая попытка решает, что показать
            last = exc
            resp = None
    if resp is None:
        raise SystemExit(f"не удалось скачать {url}: {last}")
    with resp, dest.open("wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
            out.write(chunk)
            done += len(chunk)
            if total:
                sys.stdout.write(f"\rскачано {done * 100 // total}%  ({done // 1048576} из {total // 1048576} МБ)")
                sys.stdout.flush()
    if total:
        sys.stdout.write("\n")
    return digest.hexdigest()


def app_running() -> bool:
    out = subprocess.run(["pgrep", "-f", f"{DEFAULT_APP}/Contents/MacOS/"], capture_output=True, text=True)
    return bool(out.stdout.strip())


def quit_app(wait: int = 20) -> None:
    subprocess.run(["osascript", "-e", 'quit app "NanoLika"'], capture_output=True)
    for _ in range(wait):
        if not app_running():
            return
        time.sleep(1)
    subprocess.run(["pkill", "-f", f"{DEFAULT_APP}/Contents/MacOS/"], capture_output=True)
    time.sleep(2)


def install(zip_path: Path, app: Path) -> Path:
    """Распаковать рядом, подменить приложение, вернуть путь к старой копии."""
    work = Path(tempfile.mkdtemp(prefix="update-lika-"))
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(work)
    new_app = next((p for p in work.iterdir() if p.suffix == ".app"), None)
    if new_app is None:
        raise SystemExit("в архиве нет .app")
    trash = Path.home() / ".Trash"
    trash.mkdir(exist_ok=True)
    old = trash / f"{app.name}-{installed_version(app)}-от-{time.strftime('%Y%m%d-%H%M%S')}.app"
    app.rename(old)
    shutil.copytree(new_app, app, symlinks=True)
    subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(app)], capture_output=True)
    shutil.rmtree(work, ignore_errors=True)
    return old


def main() -> int:
    ap = argparse.ArgumentParser(description="Обновить NanoLika из зеркала обновлений")
    ap.add_argument("--app", default=str(DEFAULT_APP))
    ap.add_argument("--manifest", default=os.environ.get("NANOLIKA_UPDATE_MANIFEST", DEFAULT_MANIFEST))
    ap.add_argument("--check", action="store_true", help="только проверить, не ставить")
    ap.add_argument("--dry-run", action="store_true", help="скачать и проверить, не ставить")
    ap.add_argument("--force", action="store_true", help="поставить даже ту же версию")
    args = ap.parse_args()

    app = Path(args.app)
    if not app.is_dir():
        raise SystemExit(f"нет приложения: {app}")
    current = installed_version(app)
    manifest = fetch_manifest(args.manifest)
    newest = str(manifest.get("version", "0"))
    arch, asset = asset_for(manifest)

    print(f"стоит {current} · на зеркале {newest} · сборка {arch}")
    if manifest.get("notes"):
        print(f"что нового: {manifest['notes']}")
    newer = version_tuple(newest) > version_tuple(current)
    if not newer and not args.force:
        print("обновление не нужно")
        return 0
    if args.check:
        print(f"есть новее: {newest} — запусти без --check, чтобы поставить")
        return 0

    base = args.manifest.rsplit("/", 1)[0] + "/"
    url = asset["url"] if asset["url"].startswith("http") else base + asset["url"]
    tmp = Path(tempfile.mkdtemp(prefix="update-lika-dl-"))
    zip_path = tmp / Path(asset["url"]).name
    print(f"качаю {url}")
    got = download(url, zip_path)
    want = str(asset.get("sha256", "")).lower()
    if want and got.lower() != want:
        raise SystemExit(f"контрольная сумма не совпала: ждали {want[:16]}…, получили {got[:16]}…")
    print(f"SHA-256 совпал: {got[:16]}…")
    if args.dry_run:
        print("пробный запуск: приложение не тронуто")
        return 0

    was_running = app_running()
    quit_app()
    old = install(zip_path, app)
    print(f"поставлено {installed_version(app)}; прежняя копия: {old}")
    shutil.rmtree(tmp, ignore_errors=True)
    if was_running or True:
        subprocess.run(["open", "-a", str(app)], capture_output=True)
        print("приложение запущено")
    return 0


if __name__ == "__main__":
    sys.exit(main())
