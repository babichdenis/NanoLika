"""Bounded synthetic capability probes for one model, with expiring evidence.

Dry run is the default. --run sends paid requests directly to configured suppliers.
It never reads customer conversations or credits a customer account.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import struct
import time
import zlib
from pathlib import Path

import httpx

from .config import Settings
from .provider_routing import ProviderRouter, offer_key, request_chat
from .route_quality import SUITE_VERSION


def image_fixture() -> str:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    rows = b"".join(b"\0" + b"\xff\0\0" * 32 + b"\0\0\xff" * 32 for _ in range(32))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack("!2I5B", 64, 32, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )
    return "data:image/png;base64," + base64.b64encode(png).decode()


def cases() -> list[tuple[str, dict]]:
    return [
        (
            "text",
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "Из списка дуб, липа, клён выбери слово из четырёх букв, начинающееся на л. Ответь только выбранным словом, без знаков препинания.",
                    }
                ]
            },
        ),
        (
            "json",
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "Посчитай 1234 - 567. Верни только JSON с единственным ключом result и целым числом, без пояснений.",
                    }
                ],
                "response_format": {"type": "json_object"},
            },
        ),
        (
            "tools",
            {
                "messages": [{"role": "user", "content": "Вычисли 17 * 19 и вызови record_result с результатом. Не отвечай текстом."}],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "record_result",
                            "description": "Записать вычисленный результат",
                            "parameters": {
                                "type": "object",
                                "properties": {"value": {"type": "integer"}},
                                "required": ["value"],
                                "additionalProperties": False,
                            },
                        },
                    }
                ],
                "tool_choice": "auto",
            },
        ),
        (
            "vision",
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Назови цвет левой и правой половины изображения. Верни только JSON с ключами left и right, значения на английском одним словом.",
                            },
                            {"type": "image_url", "image_url": {"url": image_fixture()}},
                        ],
                    }
                ]
            },
        ),
    ]


def text_of(data: dict) -> str:
    return str(data["choices"][0]["message"].get("content") or "").strip()


def result_passes(kind: str, data: dict) -> bool:
    try:
        if kind == "text":
            return text_of(data).lower().strip(".! \n") == "липа"
        if kind == "json":
            return json.loads(text_of(data)) == {"result": 667}
        if kind == "vision":
            raw = text_of(data).removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            return json.loads(raw) == {"left": "red", "right": "blue"}
        for call in data["choices"][0]["message"].get("tool_calls") or []:
            f = call.get("function") or {}
            if f.get("name") == "record_result" and json.loads(f.get("arguments", "")) == {"value": 323}:
                return True
    except (KeyError, IndexError, ValueError, TypeError):
        return False
    return False


async def run(args: argparse.Namespace) -> dict:
    settings = Settings()
    router = ProviderRouter(settings)
    # Probes establish admission, they must not require a certificate already.
    router.quality_required.clear()
    from .route_quality import QualityEvidence

    router.quality = QualityEvidence()
    spec = next((m for m in settings.models if m.id == args.model), None)
    if spec is None:
        raise ValueError("unknown model")
    body = {"model": spec.upstream, "messages": [{"role": "user", "content": "probe"}], "max_tokens": 1024}
    routes = router.routes(spec, body, "", {})[: args.max_routes]
    plan = [{"provider": r.provider, "model": r.body["model"]} for r in routes]
    if not args.run:
        return {"dry_run": True, "model": args.model, "routes": plan, "max_requests": len(plan) * 4, "budget_rub": args.budget_rub}
    if not routes:
        raise ValueError("no eligible supplier routes to test")
    now = int(time.time())
    target = Path(args.output)
    evidence = {"suite_version": SUITE_VERSION, "routes": {}}
    if target.exists():
        old = json.loads(target.read_text())
        if old.get("suite_version") == SUITE_VERSION:
            evidence = old
    spent = 0.0
    results = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=10), follow_redirects=False) as client:
        for route in routes:
            row = {
                "checked_at": now,
                "expires_at": now + args.ttl_hours * 3600,
                "suite_version": SUITE_VERSION,
                "checks": {},
                "latency_ms": {},
                "errors": {},
            }
            for kind, case in cases():
                estimate = route.offer.estimate(2048, 1024) if route.offer else args.budget_rub + 1
                if spent + estimate > args.budget_rub:
                    raise ValueError("probe budget exhausted; evidence was not changed")
                spent += estimate
                request_body = router._supplier_body(
                    route.provider, {**body, **case, "model": route.body["model"], "enable_thinking": False, "temperature": 0}
                )
                from dataclasses import replace

                probe = replace(route, body=request_body)
                started = time.monotonic()
                try:
                    response, _ = await request_chat(client, [probe], stream=False)
                    response.raise_for_status()
                    data = response.json()
                    row["checks"][kind] = result_passes(kind, data)
                except (httpx.HTTPError, ValueError):
                    row["checks"][kind] = False
                    row["errors"][kind] = "supplier request or response failed"
                row["latency_ms"][kind] = round((time.monotonic() - started) * 1000)
                await asyncio.sleep(0.4)
            pair = offer_key(route.provider, route.body["model"])
            evidence["routes"][pair] = row
            results.append({"provider": route.provider, "model": route.body["model"], **row})
    evidence["checked_at"] = now
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    temporary.chmod(0o600)
    temporary.replace(target)
    return {
        "model": args.model,
        "suite_version": SUITE_VERSION,
        "results": results,
        "reserved_cost_rub": round(spent, 4),
        "cost_basis": "upper estimate from supplier quotes, not confirmed debit",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--max-routes", type=int, default=3)
    parser.add_argument("--budget-rub", type=float, default=5)
    parser.add_argument("--ttl-hours", type=int, default=24)
    parser.add_argument("--output", required=True)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.max_routes <= 8 or not 0 < args.budget_rub <= 20 or not 1 <= args.ttl_hours <= 168:
        parser.error("invalid bounded probe limits")
    print(json.dumps(asyncio.run(run(args)), ensure_ascii=False))


if __name__ == "__main__":
    main()
