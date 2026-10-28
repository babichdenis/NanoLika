#!/usr/bin/env python3
"""Build the relay's model menu from two resellers' own catalogues.

Both resellers publish exact model ids and prices in rubles: routerai through
`GET /api/v1/models`, Polza through the crawl saved in `data/polza-prices-*.csv`.
This script joins them by model id, keeps the cheaper offer for each model, and
writes the `CLOUD_MODELS` list the relay reads. Prices are stored in yuan because
the relay's ledger counts yuan; the ruble figure is what the person sees, at 12 ₽
per yuan, the same rate every other price in the menu was measured with.

    python scripts/build_menu.py --out /tmp/menu.json
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

RUB_PER_YUAN = 12.0
HERE = Path(__file__).resolve().parent.parent
DATA = HERE / "data"

VENDORS = {
    "openai": "OpenAI",
    "google": "Google",
    "anthropic": "Anthropic",
    "deepseek": "DeepSeek",
    "moonshotai": "Moonshot",
    "qwen": "Qwen",
    "bytedance": "Bytedance",
    "bytedance-seed": "Bytedance",
    "mistralai": "Mistral",
    "meta-llama": "Meta",
    "x-ai": "xAI",
    "alibaba": "Alibaba",
    "minimax": "MiniMax",
    "z-ai": "Z.ai",
    "nvidia": "NVIDIA",
    "cohere": "Cohere",
    "amazon": "Amazon",
    "microsoft": "Microsoft",
    "ai21": "AI21",
    "perplexity": "Perplexity",
    "upstage": "Upstage",
    "suno": "Suno",
    "elevenlabs": "ElevenLabs",
    "kwaipilot": "Kuaishou",
    "baidu": "Baidu",
    "zhipu": "Zhipu",
    "01-ai": "01.AI",
    "nousresearch": "Nous",
    "aion-labs": "Aion",
    "anthracite-org": "Anthracite",
    "anthropic*": "Anthropic",
    "relace": "Relace",
    "inception": "Inception",
}
# что не модель для разговора и не картинка/видео/звук — в меню не идёт
SKIP_WORDS = ("embed", "rerank", "moderation", "classif", "guard", "whisper", "tts", "asr", "image", "video")


def slug(model_id: str) -> str:
    """`moonshotai/kimi-k2.7-code` -> `moonshotai-kimi-k2-7-code`, a stable, readable id."""
    text = re.sub(r"[^a-z0-9]+", "-", model_id.lower()).strip("-")
    return text[:60]


def vendor_of(model_id: str) -> str:
    head = model_id.split("/")[0].lower()
    return VENDORS.get(head, head.replace("-", " ").title())


def short_name(model_id: str, name: str) -> str:
    """`Moonshot: Kimi K2.7 Code` -> `Kimi K2.7 Code`; `OpenAI: GPT-5.4` -> `GPT-5.4`."""
    if ":" in name:
        name = name.split(":", 1)[1].strip()
    return name or model_id.split("/")[-1]


def routerai_chat() -> dict[str, dict]:
    """Text models from routerai, priced in rubles per million tokens."""
    path = sorted(DATA.glob("routerai-models-*.json"))[-1]
    out: dict[str, dict] = {}
    for model in json.loads(path.read_text())["data"]:
        arch = model.get("architecture") or {}
        modality = arch.get("modality") or ""
        if "->" not in modality or not modality.endswith("text"):
            continue
        if any(w in model["id"].lower() for w in ("embed", "rerank", "moderation", "guard")):
            continue
        pricing = model.get("pricing") or {}
        if "prompt" not in pricing:
            continue
        rub_in = float(pricing["prompt"]) * 1e6
        rub_out = float(pricing.get("completion") or 0) * 1e6
        if rub_in <= 0 and rub_out <= 0:
            continue
        out[model["id"]] = {
            "provider": "routerai",
            "rub_in": round(rub_in, 4),
            "rub_out": round(rub_out, 4),
            "name": short_name(model["id"], model.get("name") or ""),
            "vendor": vendor_of(model["id"]),
            "picture": "image" in (arch.get("input_modalities") or []),
            "context": model.get("context_length") or 0,
        }
    return out


def polza_chat() -> dict[str, dict]:
    """Text models Polza lists, from the saved crawl (prices already in rubles)."""
    path = sorted(DATA.glob("polza-prices-*.csv"))[-1]
    best: dict[str, dict] = {}
    for row in csv.DictReader(path.open()):
        if row["type"] != "chat":
            continue
        try:
            rub_in = float(row["prompt_per_million_rub"] or 0)
            rub_out = float(row["completion_per_million_rub"] or 0)
        except ValueError:
            continue
        if rub_in <= 0 and rub_out <= 0:
            continue
        total = rub_in + rub_out
        cur = best.get(row["model_id"])
        if cur and cur["total"] <= total:
            continue
        best[row["model_id"]] = {
            "provider": "polza",
            "rub_in": round(rub_in, 4),
            "rub_out": round(rub_out, 4),
            "total": total,
            "name": short_name(row["model_id"], row["name"]),
            "vendor": vendor_of(row["model_id"]),
            "picture": "image" in (row.get("input_modalities") or ""),
            "context": 0,
        }
    for offer in best.values():
        offer.pop("total", None)
    return best


def build(curated: list[dict]) -> list[dict]:
    router = routerai_chat()
    polza = polza_chat()
    taken = {m["upstream"] for m in curated if m.get("upstream")}

    menu = list(curated)
    for model_id in sorted(set(router) | set(polza)):
        if model_id in taken:
            continue
        offers = [o for o in (polza.get(model_id), router.get(model_id)) if o]
        if not offers:
            continue
        # дешевле по сумме входа и выхода: у одной компании дешевле ответ, у другой запрос
        offer = min(offers, key=lambda o: o["rub_in"] + o["rub_out"])
        yuan_in = round(offer["rub_in"] / RUB_PER_YUAN, 4)
        yuan_out = round(offer["rub_out"] / RUB_PER_YUAN, 4)
        entry = {
            "id": slug(model_id),
            "name": offer["name"][:48],
            "upstream": model_id,
            "provider": offer["provider"],
            "vendor": offer["vendor"],
            "price_in": yuan_in,
            "price_out": yuan_out,
            # что человек платит в токенах: за вход — юань за миллион, за выход — четверть
            # от этого, как у самой дешёвой модели DeepSeek, с которой всё началось
            "in_mult": max(1.0, round(yuan_in, 1)),
            "out_mult": max(1.0, round(yuan_out / 4, 1)),
            "default_on": False,
            "for": ["chat"],
        }
        if offer["picture"]:
            entry["input_modalities"] = ["text", "image"]
        menu.append(entry)

    ids = [m["id"] for m in menu]
    assert len(ids) == len(set(ids)), "повторяющиеся id: " + str([i for i in ids if ids.count(i) > 1][:5])
    return menu


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--curated", default=str(HERE / "data" / "menu-curated.json"))
    parser.add_argument("--out", default="/tmp/menu.json")
    args = parser.parse_args()

    curated = json.loads(Path(args.curated).read_text())
    menu = build(curated)
    text = json.dumps(menu, ensure_ascii=False, separators=(",", ":"))
    Path(args.out).write_text(text)

    providers: dict[str, int] = {}
    for model in menu:
        providers[model.get("provider") or "свой"] = providers.get(model.get("provider") or "свой", 0) + 1
    cheap_router = sum(1 for m in menu if m.get("provider") == "routerai")
    print(f"моделей в меню: {len(menu)} | по провайдерам: {providers}")
    print(f"размер CLOUD_MODELS: {len(text) / 1024:.1f} КБ")
    print(f"routerai выбрали для {cheap_router} моделей, остальные дешевле у Polza или свои")
    for word in ("code", "sonnet", "codestral", "qwen3-coder"):
        found = [m["name"] for m in menu if word in (m["upstream"] + m["name"]).lower()]
        print(f"  «{word}»: {len(found)}")
    print("примеры дорогих: ", [m["name"] for m in sorted(menu, key=lambda m: -m.get("price_out", 0))[:3]])


if __name__ == "__main__":
    main()
