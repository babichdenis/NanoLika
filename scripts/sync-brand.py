#!/usr/bin/env python3
"""Regenerate the bundled NanoLika marks, startup graphics and avatar copies."""

from __future__ import annotations

import importlib.util
import re
import shutil
from pathlib import Path

from brand_render import render_jobs

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "assets" / "brand"


def main() -> None:
    """Keep all packaged copies in step with the canonical brand sources."""
    spec = importlib.util.spec_from_file_location("icons", ROOT / "scripts/gen-android-icons.py")
    assert spec is not None and spec.loader is not None
    icons = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(icons)
    icons.main([])
    spec = importlib.util.spec_from_file_location("ios_icons", ROOT / "scripts/gen-ios-icons.py")
    assert spec is not None and spec.loader is not None
    ios = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ios)
    ios.main([])
    mark = (BRAND / "nanomuse-mark.svg").read_text()
    icon = (BRAND / "nanomuse-icon.svg").read_text()
    for dest in ["web/public/mark.svg", "cloud/nanomuse_cloud/console/mark.svg",
                 "demo/showcase/site/page/mark.svg"]:
        (ROOT / dest).write_text(mark)
    for dest in ["web/public/icon.svg", "cloud/nanomuse_cloud/console/icon.svg",
                 "website/public/nanomuse-icon.svg"]:
        (ROOT / dest).write_text(icon)
    jobs = []
    raster = {
        "web/public/icon-192.png": 192, "web/public/icon-512.png": 512,
        "harness/desktop/resources/app-icon.png": 1024,
        "harness/desktop/resources/logo.png": 256, "docs/app-icon.png": 512,
        "assets/brand/nanomuse-icon-source-1024.png": 1024,
    }
    raster.update({f"harness/desktop/resources/icons/{s}x{s}.png": s
                   for s in [16, 24, 32, 48, 64, 128, 256, 512]})
    for dest, size in raster.items():
        jobs.append((ROOT / dest, icon, size))
    jobs.append((BRAND / "nanomuse-mark-rgba-688.png", mark, 688))
    launch = ROOT / "android/src/ios/NanoMuse/NanoMuseLaunch.xcassets/NanoMuseLaunchMark.imageset"
    for scale in [2, 3]:
        jobs.append((launch / f"mark@{scale}x.png", mark, 120 * scale))
    # Current logo and documentation cover replace the retired blue N artwork.
    d = icons.mark_path()
    logo = f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="300" viewBox="0 0 1200 300"><rect width="1200" height="300" fill="#f7f1e6"/><g transform="translate(45 55) scale(1.9)"><path d="{d}" fill="#7e9b7b"/></g><text x="275" y="200" font-family="Georgia,serif" font-style="italic" font-size="145" fill="#38372f">nano<tspan fill="#52694e">Lika</tspan></text><circle cx="970" cy="100" r="9" fill="#bd705b"/></svg>'
    (BRAND / "nanomuse-cover.svg").write_text(logo)
    for dest in ["assets/brand/nanomuse-cover.png", "assets/brand/nanoMuse-logo-and-text.png", "docs/cover.png"]:
        jobs.append((ROOT / dest, logo, 1200))
    render_jobs(jobs)
    # Match the same mark in the inline desktop and recorded-demo components.
    d = icons.mark_path()
    for dest in ["harness/dsh-nanomuse/src/client/BrandMark.tsx",
                 "demo/mobilegym/apps/nanoMuse/res/mark.tsx"]:
        p = ROOT / dest
        text = p.read_text()
        text = re.sub(r"M(?:50\.02 79\.14|49 71)[^']+", d, text)
        p.write_text(text)
    avatar = BRAND / "avatar"
    for mood in ["idle", "working", "waiting", "happy", "error"]:
        for ext in ["webp", "mp4"]:
            source = avatar / f"{mood}.{ext}"
            if not source.exists():
                continue
            for folder in ["web/public/avatars", "harness/dsh-nanomuse/assets",
                           "android/src/ios/NanoMuse/Resources"]:
                shutil.copyfile(source, ROOT / folder / f"dragon-{mood}.{ext}")
            res = ROOT / "android/src/android/app/src/main/res"
            dest = res / ("raw" if ext == "mp4" else "drawable-nodpi")
            name = "nm_motion" if ext == "mp4" else "nm_avatar"
            shutil.copyfile(source, dest / f"{name}_{mood}.{ext}")
    if (avatar / "idle.webp").exists():
        shutil.copyfile(avatar / "idle.webp", ROOT / "demo/mobilegym/apps/nanoMuse/res/dragon-idle.webp")


if __name__ == "__main__":
    main()
