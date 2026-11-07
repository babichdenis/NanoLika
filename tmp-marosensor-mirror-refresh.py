#!/usr/bin/env python3
"""Свежая статическая копия marosensor.ru для резервного режима.

Качает сайт wget'ом во временный каталог, чинит ссылки WordPress,
добавляет баннер «резервная копия» и атомарно переключает symlink current.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

BASE = "/srv/marosensor-mirror"
RELEASES = os.path.join(BASE, "releases")
CURRENT = os.path.join(BASE, "current")
SITE = "https://marosensor.ru/"
KEEP = 3


def run(cmd, **kwargs):
    return subprocess.run(cmd, check=True, text=True, **kwargs)


def crawl(dest: str) -> None:
    os.makedirs(dest, exist_ok=True)
    proc = subprocess.run([
        "wget", "--mirror", "--page-requisites", "--adjust-extension",
        "--convert-links", "--no-parent", "--domains", "marosensor.ru",
        "--execute", "robots=off",
        "--user-agent=Mozilla/5.0 (compatible; MarosensorMirror/1.0)",
        "--timeout=25", "--tries=2", "--directory-prefix", dest, SITE,
    ], capture_output=True, text=True)
    # wget возвращает 8, если часть ссылок отдала 4xx/5xx; страницы при этом скачаны.
    if proc.returncode not in (0, 8):
        raise SystemExit(f"wget failed: {proc.returncode}\n{proc.stderr[-1500:]}")


def build_idmap(root: str) -> dict:
    ids = set()
    for dp, _, files in os.walk(root):
        for f in files:
            if not f.endswith(".html"):
                continue
            text = open(os.path.join(dp, f), encoding="utf-8", errors="ignore").read()
            ids.update(re.findall(r"(?:%3F|\?)p=(\d+)", text))
    mapping = {}
    for pid in sorted(ids, key=int):
        out = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{redirect_url}",
             "-m", "20", f"https://marosensor.ru/?p={pid}"],
            capture_output=True, text=True,
        ).stdout.strip()
        if not out:
            out = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{url_effective}",
                 "-L", "-m", "20", f"https://marosensor.ru/?p={pid}"],
                capture_output=True, text=True,
            ).stdout.strip()
        path = re.sub(r"^https?://[^/]+", "", out)
        path = path.split("?")[0]
        if path:
            mapping[pid] = path
    return mapping


BANNER = (
    '<div style="position:relative;z-index:99999;background:#0a1628;color:#f5efe6;'
    "font:15px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;"
    'padding:10px 18px;text-align:center">'
    '<strong>Резервная копия сайта.</strong> Основной сервер временно недоступен. '
    'Заявки: <a style="color:#f0c987" href="mailto:office@memsnav.ru">office@memsnav.ru</a>, '
    'телефон <a style="color:#f0c987" href="tel:+78127701703">+7 (812) 770-17-03</a>.'
    "</div>"
)

CONTACT = (
    '<div style="border:1px solid #d8cdbd;background:#faf6ef;padding:18px 20px;margin:24px 0">'
    "<strong>Сайт работает в резервном режиме, форма недоступна.</strong><br>"
    'Пришлите заявку на <a href="mailto:office@memsnav.ru">office@memsnav.ru</a> '
    'или позвоните <a href="tel:+78127701703">+7 (812) 770-17-03</a>.'
    "</div>"
)


def rel_target(target_path: str, file_dir: str, root: str) -> str:
    target = target_path.lstrip("/")
    if target.endswith("/"):
        target += "index.html"
    elif not os.path.splitext(target)[1]:
        target += "/index.html"
    return os.path.relpath(os.path.join(root, target), file_dir).replace(os.sep, "/")


def normalize(root: str, idmap: dict) -> None:
    for dp, _, files in os.walk(root):
        for fn in files:
            if "?" not in fn:
                continue
            src = os.path.join(dp, fn)
            base = fn.split("?", 1)[0]
            if base in ("index.html", "index.htm"):
                os.remove(src)
                continue
            dst = os.path.join(dp, base)
            if os.path.exists(dst):
                os.remove(src)
            else:
                os.rename(src, dst)

    for dp, _, files in os.walk(root):
        for fn in files:
            if not fn.endswith(".html"):
                continue
            path = os.path.join(dp, fn)
            s = open(path, encoding="utf-8", errors="ignore").read()
            orig = s

            def page_repl(m):
                target = idmap.get(m.group(1))
                if not target:
                    return m.group(0)
                return rel_target(target, dp, root)

            s = re.sub(r"index\.html(?:%3F|\?)p=(\d+)\.html", page_repl, s)
            s = re.sub(r"(?:%3F|\?)ver=[0-9A-Za-z.\-_]+(\.[a-z]+)?", "", s)
            s = re.sub(r"<link[^>]+wp-json/oembed[^>]*>", "", s)
            s = re.sub(r"<link[^>]+rel=[\"']https://api\.w\.org/[\"'][^>]*>", "", s)
            s = re.sub(r"<form[^>]*request-form[^>]*>.*?</form>", CONTACT, s, flags=re.S | re.I)
            if "</body>" in s and "Резервная копия сайта." not in s:
                s = re.sub(r"(<body[^>]*>)", r"\1" + BANNER, s, count=1, flags=re.I)
            if s != orig:
                open(path, "w", encoding="utf-8").write(s)

    shutil.rmtree(os.path.join(root, "wp-json"), ignore_errors=True)
    shutil.rmtree(os.path.join(root, "wp-content/cache"), ignore_errors=True)


def verify(root: str) -> None:
    pages = 0
    for dp, _, files in os.walk(root):
        pages += sum(1 for f in files if f == "index.html")
    if pages < 55:
        raise SystemExit(f"mirror looks incomplete: {pages} pages")
    home = open(os.path.join(root, "index.html"), encoding="utf-8", errors="ignore").read()
    if "Резервная копия сайта." not in home:
        raise SystemExit("mirror banner missing")
    if not os.path.exists(os.path.join(root, "kontakty", "index.html")):
        raise SystemExit("contacts page missing")


def prune() -> None:
    releases = sorted(
        (d for d in os.listdir(RELEASES) if os.path.isdir(os.path.join(RELEASES, d))),
        reverse=True,
    )
    for old in releases[KEEP:]:
        shutil.rmtree(os.path.join(RELEASES, old), ignore_errors=True)


def main() -> None:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    tmp = os.path.join(RELEASES, f".tmp-{stamp}")
    final = os.path.join(RELEASES, stamp)
    os.makedirs(RELEASES, exist_ok=True)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"crawling {SITE} -> {tmp}")
    crawl(tmp)
    root = os.path.join(tmp, "marosensor.ru")
    if not os.path.isdir(root):
        raise SystemExit("wget produced no site directory")
    print("building id map")
    idmap = build_idmap(root)
    print(f"ids: {len(idmap)}")
    normalize(root, idmap)
    verify(root)
    os.rename(tmp, final)

    link_tmp = CURRENT + ".new"
    if os.path.lexists(link_tmp):
        os.remove(link_tmp)
    os.symlink(os.path.join("releases", stamp, "marosensor.ru"), link_tmp)
    os.replace(link_tmp, CURRENT)
    prune()
    print(f"current -> {final}")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f"command failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
