#!/usr/bin/env python3
"""Сколько на самом деле стоит одна картинка у поставщика с ценой за токены.

ProxyAPI (и RouterAI) продают картинки не за штуку, а за 1M токенов. По прайсу
нельзя понять цену генерации: у FLUX 3 Klein на 1K уходит ~4 000 токенов, у
Seedream 5 — ~16 000. Скрипт делает одну картинку, читает `usage` из ответа и
сверяет его с изменением баланса.

    PROXYAPI_KEY=... python cloud/scripts/measure_image_cost.py \
        --model black-forest-labs/flux-3-image-klein --resolution 1K

Ключ не печатается. Одна генерация стоит от ~1 ₽ (FLUX Klein) до ~25 ₽
(Seedream 5 Pro), поэтому по умолчанию ничего не запускается без --yes.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request


def call(base: str, key: str, path: str, payload: dict | None = None, timeout: int = 300) -> tuple[int, bytes]:
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode() if payload else None,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
        return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def balance(base: str, key: str) -> float | None:
    status, body = call(base, key, "/proxyapi/balance", timeout=30)
    if status != 200:
        return None
    try:
        return round(float(json.loads(body)["balance"]), 6)
    except (ValueError, KeyError, TypeError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="black-forest-labs/flux-3-image-klein")
    parser.add_argument("--resolution", default="1K")
    parser.add_argument("--aspect-ratio", default="1:1")
    parser.add_argument("--prompt", default="A small red cube on a white table")
    parser.add_argument("--base", default="https://api.proxyapi.ru")
    parser.add_argument("--yes", action="store_true", help="действительно сгенерировать картинку")
    args = parser.parse_args()

    key = os.environ.get("PROXYAPI_KEY", "").strip()
    if not key:
        print("нужен PROXYAPI_KEY в окружении")
        return 2
    if not args.yes:
        print(f"будет одна генерация: {args.model}, {args.resolution}, {args.aspect_ratio}")
        print("повторите с --yes, когда готовы потратить деньги")
        return 0

    before = balance(args.base, key)
    payload = {
        "model": args.model,
        "prompt": args.prompt,
        "resolution": args.resolution,
        "aspect_ratio": args.aspect_ratio,
        "n": 1,
    }
    started = time.time()
    status, body = call(args.base, key, "/v1/images/generations", payload)
    seconds = round(time.time() - started, 1)
    if status != 200:
        print(f"отказ {status}: {body[:400].decode('utf-8', 'replace')}")
        return 1
    try:
        answer = json.loads(body)
    except ValueError:
        print("ответ не JSON")
        return 1
    after = balance(args.base, key)
    usage = answer.get("usage") or {}
    cost = round(before - after, 6) if before is not None and after is not None else None
    print(
        json.dumps(
            {
                "model": args.model,
                "resolution": args.resolution,
                "seconds": seconds,
                "usage": usage,
                "cost_rub": cost,
                "balance_before": before,
                "balance_after": after,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
