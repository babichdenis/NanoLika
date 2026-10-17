#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Текстовый «просмотр» PNG: ASCII по альфе, по яркости или по палитре."""
import sys

import numpy as np
from PIL import Image

CH = " .:-=+*#%@"

PALETTE = [
    ("#C05B3C", "T", "terracotta"),
    ("#E4795A", "C", "coral"),
    ("#F3B693", "P", "peach"),
    ("#FBF1E6", "k", "cream"),
    ("#7E9B77", "S", "sage"),
    ("#3A2E28", "N", "ink"),
    ("#FFFFFF", "W", "white"),
    ("#000000", "B", "black"),
]


def _hx(v):
    v = v.lstrip("#")
    return np.array([int(v[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float32)


def show(path, cols=64, rows=32, mode="alpha"):
    img = Image.open(path).convert("RGBA")
    im = img.resize((cols, rows * 2), Image.BOX)
    a = np.asarray(im, dtype=np.float32) / 255.0
    orig = np.asarray(img, dtype=np.float32) / 255.0

    # заливка (opaque) в исходном разрешении
    cov = float((orig[..., 3] > 0.5).mean())
    print(f"--- {path}  {img.size}  mode={mode}  заливка={cov*100:.1f}%")

    if mode == "color":
        names = np.array([_hx(c[0]) for c in PALETTE]) / 255.0
        for y in range(rows):
            line = ""
            for x in range(cols):
                blk = a[y * 2:(y + 1) * 2, x]
                idx = np.argmax(blk[:, 3])
                px = blk[idx]
                if px[3] < 0.25:
                    line += " "
                    continue
                # цвет блока = самый непрозрачный пиксель (RGB без премультипликации)
                d = ((names - px[:3]) ** 2).sum(axis=1)
                line += PALETTE[int(np.argmin(d))][1]
            print(line)
        print()
        return

    if mode == "lum":
        rgb = a[..., :3] * a[..., 3:4]
        v = rgb.sum(axis=2) / 3
        v = v / max(v.max(), 1e-6)
    else:
        v = a[..., 3]

    for y in range(rows):
        line = ""
        for x in range(cols):
            blk = v[y * 2:(y + 1) * 2, x].mean()
            line += CH[min(len(CH) - 1, int(blk * len(CH)))]
        print(line)
    print()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    mode = "alpha"
    for flag in ("--lum", "--color"):
        if flag in sys.argv:
            mode = flag[2:]
    for p in args:
        m = mode
        if "--auto" in sys.argv and "wordmark" in p:
            m = "lum"
        show(p, 80 if "wordmark" in p else 64, 12 if "wordmark" in p else 30, m)
