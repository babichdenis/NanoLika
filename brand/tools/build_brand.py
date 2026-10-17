#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сборка бренд-ассетов NanoLika: три знака + вордмарк.

Геометрию рисуем сами (PIL) в 4x-суперсэмплинге, уменьшаем в
premultiplied-альфе — иначе на краях вылезает тёмная кайма.
Всё детерминировано и проверяемо пиксельно; фон честно прозрачный.
"""
import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

BRAND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SS = 4                      # суперсэмплинг
CANVAS = 512 * SS           # рабочее полотно знака
FILL = 0.88                 # какую долю итогового квадрата занимает знак

FONT_TTC = "/System/Library/Fonts/Avenir Next.ttc"
FONT_IDX_BOLD = 0           # Avenir Next Bold
FONT_IDX_DEMI = 2           # Avenir Next Demi Bold

PALETTE = {
    "terracotta": "#C05B3C",
    "coral":      "#E4795A",
    "peach":      "#F3B693",
    "cream":      "#FBF1E6",
    "sage":       "#7E9B77",
    "ink":        "#3A2E28",
    "white":      "#FFFFFF",
}


def hx(value):
    v = value.lstrip("#")
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4)) + (255,)


# ---------------------------------------------------------------- геометрия

def quad(p0, c, p1, steps=160):
    """Квадратичная кривая Безье, разложенная в точки."""
    out = []
    for i in range(steps + 1):
        t = i / steps
        mt = 1 - t
        x = mt * mt * p0[0] + 2 * mt * t * c[0] + t * t * p1[0]
        y = mt * mt * p0[1] + 2 * mt * t * c[1] + t * t * p1[1]
        out.append((x, y))
    return out


def spark_arcs(cx, cy, r, waist, steps=400):
    """4 дуги Безье: от луча к лучу, с вогнутой «талией».

    waist — радиус талии между лучами (по диагонали 45°) в долях r.
    Точка квадратичной кривой при t=0.5 отстоит от центра на
    0.35355*r + 0.5*rc, отсюда радиус контрольной точки:
        rc = (waist - 0.35355) * 2 * r
    """
    rc = (waist - 0.35355) * 2 * r
    arcs = []
    for k in range(4):
        a0 = math.radians(-90 + 90 * k)
        a1 = math.radians(-90 + 90 * (k + 1))
        am = math.radians(-90 + 90 * k + 45)
        arcs.append(quad((cx + r * math.cos(a0), cy + r * math.sin(a0)),
                         (cx + rc * math.cos(am), cy + rc * math.sin(am)),
                         (cx + r * math.cos(a1), cy + r * math.sin(a1)),
                         steps=steps))
    return arcs


def _tip_exit(arc, ctr, rt, at_end):
    """Индекс, с которого дуга выходит из круга кончика (идём от кончика внутрь)."""
    limit = rt * (1 + 1e-6)
    idx = range(len(arc) - 1, 0, -1) if at_end else range(1, len(arc))
    for i in idx:
        p = arc[i]
        if math.hypot(p[0] - ctr[0], p[1] - ctr[1]) > limit:
            return i
    return 0 if at_end else len(arc) - 1


def spark_poly(cx, cy, r, waist=0.30, steps=400):
    """Острые лучи искорки (вогнутые бока), замкнутый контур без самопересечений."""
    return [p for arc in spark_arcs(cx, cy, r, waist, steps) for p in arc]


def draw_spark(draw, cx, cy, r, waist, tip_r, color):
    """Искорка со скруглёнными кончиками: объединение острых лучей и кругов.

    Лучи рисуем с радиусом r-tip_r*r (вершины попадают в центры кругов),
    затем добавляем 4 круга радиуса tip_r*r на осях лучей — их внешняя
    кромка и даёт искомый радиус r.
    """
    rt = tip_r * r
    r2 = r - rt
    draw.polygon(spark_poly(cx, cy, r2, waist * r / r2 if r2 else waist), fill=color)
    for k in range(4):
        a = math.radians(-90 + 90 * k)
        tcx, tcy = cx + r2 * math.cos(a), cy + r2 * math.sin(a)
        draw.ellipse([tcx - rt, tcy - rt, tcx + rt, tcy + rt], fill=color)


def superellipse(cx, cy, rx, ry, n=4.0, steps=720):
    """Скруглённый квадрат (squircle): |x/rx|^n + |y/ry|^n = 1."""
    pts = []
    for i in range(steps):
        a = 2 * math.pi * i / steps
        ca, sa = math.cos(a), math.sin(a)
        x = rx * math.copysign(abs(ca) ** (2.0 / n), ca)
        y = ry * math.copysign(abs(sa) ** (2.0 / n), sa)
        pts.append((cx + x, cy + y))
    return pts


def vesica(p0, p1, bulge, steps=220):
    """Лист: две дуги от кончика до кончика, раздутые в обе стороны."""
    mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    ln = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / ln, dx / ln
    c1 = (mx + nx * bulge, my + ny * bulge)
    c2 = (mx - nx * bulge, my - ny * bulge)
    return quad(p0, c1, p1, steps) + quad(p1, c2, p0, steps)[1:]


def thick_line(draw, p0, p1, width, color):
    """Отрезок с круглыми торцами (у PIL нет round cap)."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    ln = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / ln * width / 2, dx / ln * width / 2
    draw.polygon([(p0[0] + nx, p0[1] + ny), (p1[0] + nx, p1[1] + ny),
                  (p1[0] - nx, p1[1] - ny), (p0[0] - nx, p0[1] - ny)], fill=color)
    for p in (p0, p1):
        draw.ellipse([p[0] - width / 2, p[1] - width / 2,
                      p[0] + width / 2, p[1] + width / 2], fill=color)


def rotate(pts, deg, cx, cy):
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    return [(cx + (x - cx) * ca - (y - cy) * sa,
             cy + (x - cx) * sa + (y - cy) * ca) for x, y in pts]


# ------------------------------------------------------------- ресемплинг

def _rs(arr, size, resample):
    im = Image.fromarray(np.clip(arr * 255.0, 0, 255).astype(np.uint8))
    return np.asarray(im.resize(size, resample), dtype=np.float32) / 255.0


def resize_premul(img, size, resample=Image.BOX):
    """Масштабирование RGBA в premultiplied-альфе: без тёмной каймы."""
    a = np.asarray(img, dtype=np.float32) / 255.0
    rgb, alpha = a[..., :3], a[..., 3]
    pm = _rs(rgb * alpha[..., None], size, resample)
    al = _rs(alpha, size, resample)
    safe = np.where(al > 1e-4, al, 1.0)
    out = np.zeros((size[1], size[0], 4), dtype=np.uint8)
    out[..., :3] = np.clip(pm / safe[..., None] * 255.0, 0, 255).astype(np.uint8)
    out[..., 3] = np.clip(al * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def finish(img, out_path, base=512, fill=FILL):
    """Обрезать по альфе, отцентровать, отмасштабировать, сохранить."""
    bbox = img.split()[3].getbbox()
    img = img.crop(bbox)
    w, h = img.size
    target = base * SS * fill
    scale = target / max(w, h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    if (nw, nh) != (w, h):
        img = resize_premul(img, (nw, nh), Image.LANCZOS)
    stage = Image.new("RGBA", (base * SS, base * SS), (0, 0, 0, 0))
    stage.paste(img, ((base * SS - nw) // 2, (base * SS - nh) // 2), img)
    small = resize_premul(stage, (base, base), Image.BOX)
    small.save(out_path)
    return small


# ------------------------------------------------------------------- знаки

def icon_spark():
    """Тёплая искорка: 4 округлых лепестка, один цвет, читается с 32 px."""
    img = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    draw_spark(d, CANVAS // 2, CANVAS // 2, 640, waist=0.30, tip_r=0.27,
               color=hx(PALETTE["coral"]))
    return img


def icon_monogram():
    """Монограмма NL: кремовые буквы на терракотовой плитке-сквиркле."""
    img = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = CANVAS // 2
    side = 1792
    d.polygon(superellipse(cx, cy, side / 2, side / 2, n=4.0),
              fill=hx(PALETTE["terracotta"]))
    draw_tracked_text(d, "NL", cx, cy, target_w=side * 0.60,
                      color=hx(PALETTE["cream"]), weight=FONT_IDX_BOLD,
                      tracking_em=-0.02, kern={("N", "L"): -0.02})
    return img


def icon_leaf():
    """Лист в тёплом круге: шалфей на персике, прожилка — просвет персика."""
    img = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = CANVAS // 2
    r_circle = 900
    d.ellipse([cx - r_circle, cy - r_circle, cx + r_circle, cy + r_circle],
              fill=hx(PALETTE["peach"]))

    L = 1420
    leaf = vesica((cx - L / 2, cy), (cx + L / 2, cy), bulge=0.34 * L)
    d.polygon(rotate(leaf, -34, cx, cy), fill=hx(PALETTE["sage"]))

    a = rotate([(cx - L / 2 + 0.12 * L, cy)], -34, cx, cy)[0]
    b = rotate([(cx - L / 2 + 0.88 * L, cy)], -34, cx, cy)[0]
    thick_line(d, a, b, 0.055 * L, hx(PALETTE["peach"]))
    return img


# ------------------------------------------------------------------ текст

def font(size, weight=FONT_IDX_DEMI):
    return ImageFont.truetype(FONT_TTC, size, index=weight)


def cap_height(f):
    return -f.getbbox("N", anchor="ls")[1]


def draw_tracked_text(draw, text, cx, cy, target_w, color,
                      tracking_em=0.0, kern=None, weight=FONT_IDX_DEMI):
    """Текст с ручным трекингом: кегль подгоняем под целевую ширину."""
    kern = kern or {}
    probe = 100.0
    em = 100.0

    def advance(size):
        f = font(int(round(size)), weight)
        w = 0.0
        for i, ch in enumerate(text):
            w += f.getlength(ch)
            if i < len(text) - 1:
                w += size * tracking_em + size * kern.get((ch, text[i + 1]), 0.0)
        return w

    size = probe * target_w / advance(em)
    f = font(int(round(size)), weight)
    total = advance(size)
    baseline = cy + cap_height(f) / 2          # центрируем по высоте прописной
    x = cx - total / 2
    for i, ch in enumerate(text):
        draw.text((x, baseline), ch, font=f, fill=color, anchor="ls")
        x += f.getlength(ch)
        if i < len(text) - 1:
            x += size * tracking_em + size * kern.get((ch, text[i + 1]), 0.0)


def wordmark(text, color_hex, out_name, width=1200):
    """Вордмарк: ширина итогового PNG ровно `width`, фон прозрачный."""
    pad_y = 26
    big = Image.new("RGBA", (6000, 1600), (0, 0, 0, 0))
    draw_tracked_text(ImageDraw.Draw(big), text, 3000, 800, 4200,
                      hx(color_hex), tracking_em=0.006,
                      kern={("L", "i"): -0.03})
    crop = big.crop(big.split()[3].getbbox())
    w, h = crop.size
    inner = int(width * 0.96)
    nh = max(1, round(h * inner / w))
    crop = resize_premul(crop, (inner, nh), Image.LANCZOS)
    canvas = Image.new("RGBA", (width, nh + 2 * pad_y), (0, 0, 0, 0))
    canvas.paste(crop, ((width - inner) // 2, pad_y), crop)
    canvas.save(os.path.join(BRAND, out_name))
    return canvas


# ---------------------------------------------------------- превью и отчёт

def colors_at(im, n=32):
    small = resize_premul(im, (n, n), Image.BOX)
    a = np.asarray(small).reshape(-1, 4)
    solid = a[a[:, 3] > 200]
    if len(solid) == 0:
        return 0.0, []
    cols, counts = np.unique(solid[:, :3], axis=0, return_counts=True)
    order = np.argsort(-counts)[:4]
    return len(solid) / (n * n), [(tuple(int(v) for v in cols[i]), int(counts[i]))
                                 for i in order]


def paste_center(sheet, im, cx, cy):
    sheet.paste(im, (round(cx - im.size[0] / 2), round(cy - im.size[1] / 2)), im)


def contact_sheet(icons, wordmarks, out_path):
    cw, ch, half = 1600, 780, 390
    sheet = Image.new("RGB", (cw, ch), hx(PALETTE["cream"])[:3])
    d = ImageDraw.Draw(sheet)
    d.rectangle([0, half, cw, ch], fill=hx("#2A2320")[:3])

    def half_block(y0, dark):
        for i, name in enumerate(icons):
            paste_center(sheet, icons[name], 150 + 240 * i, y0 + 120)
            for s in (32,):
                paste_center(sheet, resize_premul(icons[name], (s, s)), 150 + 240 * i, y0 + 300)
            d.ellipse([150 + 240 * i - 70, y0 + 240 - 70,
                       150 + 240 * i + 70, y0 + 240 + 70], outline=hx("#00000030")[:3] if not dark else hx("#FFFFFF30")[:3], width=1)
        key = "wordmark-nanolika-white-1200.png" if dark else "wordmark-nanolika-dark-1200.png"
        wm = wordmarks[key]
        w = 760
        h = max(1, round(wm.size[1] * w / wm.size[0]))
        paste_center(sheet, resize_premul(wm, (w, h), Image.LANCZOS), 1180, y0 + 195)

    half_block(0, False)
    half_block(half, True)
    sheet.save(out_path)


def main():
    icons, report = {}, []
    raw = {
        "icon-spark-512.png": icon_spark(),
        "icon-monogram-512.png": icon_monogram(),
        "icon-leaf-512.png": icon_leaf(),
    }
    for name, img in raw.items():
        path = os.path.join(BRAND, name)
        small = finish(img, path)
        icons[name] = small
        cov, top = colors_at(small, 32)
        report.append((name, small.mode, small.size, cov, top))
        for n in (64, 32):
            resize_premul(small, (n, n), Image.BOX).save(
                os.path.join(BRAND, "previews", name.replace("-512.png", f"-{n}.png")))

    wordmarks = {}
    for name, col in (("wordmark-nanolika-dark-1200.png", PALETTE["ink"]),
                      ("wordmark-nanolika-white-1200.png", PALETTE["white"])):
        wordmarks[name] = wordmark("NanoLika", col, name, width=1200)

    contact_sheet(icons, wordmarks, os.path.join(BRAND, "previews", "contact-sheet.png"))

    print("== знаки ==")
    for name, mode, size, cov, top in report:
        tops = ", ".join("#%02X%02X%02X x%d" % (c[0], c[1], c[2], k) for c, k in top)
        print(f"{name}: {mode} {size[0]}x{size[1]}, заливка@32px {cov*100:.0f}% | {tops}")
    print("== вордмарки ==")
    for name, im in wordmarks.items():
        print(f"{name}: {im.mode} {im.size[0]}x{im.size[1]}")


if __name__ == "__main__":
    os.makedirs(os.path.join(BRAND, "previews"), exist_ok=True)
    main()
