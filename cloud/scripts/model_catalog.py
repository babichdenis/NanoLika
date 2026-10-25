#!/usr/bin/env python3
"""Энциклопедия моделей: наши поставщики, мировые цены, возможности, качество.

Собирает в один JSON всё, что нужно, чтобы решить «какую модель взять и у кого дешевле»:

  * **наши поставщики** — из снимка `console/providers/prices.json` (его делает
    :mod:`nanomuse_cloud.provider_prices`): рублёвая цена каждого продавца;
  * **мировые цены и возможности** — из карты цен LiteLLM
    (`model_prices_and_context_window.json`, 4300 моделей): $ за 1M вход/выход, контекст,
    модальности (картинки, звук, видео, pdf), инструменты, размышления, кэш;
  * **качество** — с artificialanalysis.ai (272 модели): индекс интеллекта, цена в $,
    open-weights, скорость размышления, бенчмарки.

Три источника сводятся по имени модели: LiteLLM даёт точный ключ и проверенные метаданные,
artificialanalysis — оценку качества, наш снимок — рубли у конкретного продавца. Модель,
которой нет у нас, остаётся в таблице как «на рынке, у нас нет» — это и есть ответ на
вопрос «что стоит добавить в меню».

    python3 cloud/scripts/model_catalog.py                  # console/models/catalog.json
    python3 cloud/scripts/model_catalog.py --out /tmp/c.json
    python3 cloud/scripts/model_catalog.py --check          # устарел ли файл

Ничего не решает и никого не переключает: только данные. Страница читает готовый файл.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "cloud" / "nanomuse_cloud" / "console" / "providers" / "prices.json"
TARGET = ROOT / "cloud" / "nanomuse_cloud" / "console" / "prices" / "catalog.json"

LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
)
AA_URL = "https://artificialanalysis.ai/leaderboards/models"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
TIMEOUT = 60

#: Поставщики, которых мы показываем колонками. Порядок — как в админке.
OUR_PROVIDERS = (
    "routerai",
    "polza",
    "aitunnel",
    "proxyapi",
    "proxyapi-openai",
    "bothub",
    "neuroapi",
    "openrouter",
    "own",
    "deepseek",
)

#: Мировые продавцы, которые LiteLLM знает по имени: их цена — «мировая».
WORLD_PROVIDERS = (
    "openai",
    "anthropic",
    "gemini",
    "deepseek",
    "xai",
    "mistral",
    "groq",
    "together_ai",
    "fireworks_ai",
    "openrouter",
    "bedrock",
    "vertex_ai",
    "azure",
)

#: Модальности, которые выносим в колонки-галочки.
MODALITIES = {
    "image": "картинки на вход",
    "audio_in": "звук на вход",
    "audio_out": "звук на выход",
    "video": "видео на вход",
    "pdf": "PDF на вход",
}

FAMILY_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("deepseek", re.compile(r"\bdeepseek\b", re.I)),
    ("anthropic", re.compile(r"\b(claude|anthropic)\b", re.I)),
    ("openai", re.compile(r"\b(gpt|o1|o3|o4|chatgpt|davinci)\b", re.I)),
    ("google", re.compile(r"\b(gemini|gemma|palm)\b", re.I)),
    ("xai", re.compile(r"\b(grok|x-ai|xai)\b", re.I)),
    ("moonshot", re.compile(r"\b(kimi|moonshot)\b", re.I)),
    ("zhipu", re.compile(r"\b(glm|z-ai|zhipu|chatglm)\b", re.I)),
    ("qwen", re.compile(r"\b(qwen|qwq|tongyi|dashscope)\b", re.I)),
    ("meta", re.compile(r"\b(llama|meta)\b", re.I)),
    ("mistral", re.compile(r"\b(mistral|mixtral|codestral|magistral)\b", re.I)),
    ("cohere", re.compile(r"\b(command|cohere)\b", re.I)),
    ("ai21", re.compile(r"\b(jamba|ai21)\b", re.I)),
    ("amazon", re.compile(r"\b(nova|titan)\b", re.I)),
    ("microsoft", re.compile(r"\b(phi|azure)\b", re.I)),
    ("nvidia", re.compile(r"\b(nemotron|nvidia)\b", re.I)),
    ("baidu", re.compile(r"\b(ernie)\b", re.I)),
    ("bytedance", re.compile(r"\b(doubao|seed|byteplus)\b", re.I)),
)


# --------------------------------------------------------------------------- fetching
def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read()


def litellm_rows() -> dict[str, dict[str, Any]]:
    """Карта LiteLLM: ключ модели → её метаданные."""
    data = json.loads(fetch(LITELLM_URL).decode("utf-8"))
    return {k: v for k, v in data.items() if isinstance(v, dict) and v.get("litellm_provider")}


def artificial_analysis() -> list[dict[str, Any]]:
    """Модели с artificialanalysis.ai: они лежат в самой странице, в потоке Next.js."""
    page = fetch(AA_URL).decode("utf-8", errors="replace")
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', page, re.S)
    blob = "".join(chunks).encode().decode("unicode_escape", errors="ignore")
    at = blob.find('"initialModels":[')
    if at < 0:
        return []
    start = blob.index("[", at)
    depth = 0
    for index in range(start, len(blob)):
        if blob[index] == "[":
            depth += 1
        elif blob[index] == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(blob[start : index + 1])
                except ValueError:
                    return []
    return []


# --------------------------------------------------------------------------- matching
def slug(name: str) -> str:
    """Имя модели без поставщика, суффиксов и скобок: ``openai/gpt-5 (Xhigh)`` → ``gpt-5``.

    Скобки в именах artificialanalysis - это режим или вариант прогона ("(Medium, Default
    Fallback)", "(Non-reasoning)", "(Xhigh)"): качество у них то же, поэтому в ключ они не
    входят, иначе половина моделей не находит свою пару в LiteLLM."""
    text = (name or "").strip().lower()
    text = re.sub(r"\([^)]*\)", " ", text)  # варианты прогона
    text = text.split(":")[0]  # bedrock-артефакты вида model-v1:0
    text = re.sub(r"^(openai|anthropic|google|meta|xai|mistralai|deepseek|qwen|moonshotai)/", "", text)
    text = text.split("/")[-1]
    text = re.sub(r"[-_\s]*(preview|latest|stable|exp|experimental|beta|rc\d*)$", "", text)
    text = re.sub(r"-(20\d\d)(\d\d)?(\d\d)?$", "", text)  # даты релизов
    return re.sub(r"[^a-z0-9.]+", "-", text).strip("-")


def family_of(name: str) -> str:
    for family, pattern in FAMILY_RULES:
        if pattern.search(name or ""):
            return family
    return "other"


def capabilities(row: dict[str, Any]) -> dict[str, bool]:
    """Во что модель умеет: по флагам LiteLLM."""
    mode = str(row.get("mode") or "chat")
    return {
        "text": mode in ("chat", "completion", "responses", "realtime"),
        "code": bool(row.get("supports_function_calling")) or mode == "chat",
        "tools": bool(row.get("supports_function_calling")),
        "image": bool(row.get("supports_vision") or row.get("supports_image_input")),
        "audio_in": bool(row.get("supports_audio_input")),
        "audio_out": bool(row.get("supports_audio_output")),
        "video": bool(row.get("supports_video_input")),
        "pdf": bool(row.get("supports_pdf_input")),
        "reasoning": bool(row.get("supports_reasoning") or row.get("thinking_always_on")),
        "open_weights": False,  # заполняется из artificialanalysis
        "caching": bool(row.get("supports_prompt_caching")),
        "structured": bool(
            row.get("supports_response_schema") or row.get("supports_native_structured_output")
        ),
        "computer_use": bool(row.get("supports_computer_use")),
        "web_search": bool(row.get("supports_web_search")),
    }


def per_million(value: Any) -> float | None:
    """Цена за токен из LiteLLM ($/token) → $ за 1M токенов."""
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    return round(float(value) * 1_000_000, 4)


def litellm_card(key: str, row: dict[str, Any]) -> dict[str, Any]:
    mode = str(row.get("mode") or "chat")
    context = row.get("max_input_tokens") or row.get("max_tokens")
    return {
        "name": key,
        "family": family_of(key),
        "provider": row.get("litellm_provider"),
        "mode": mode,
        "context": int(context) if isinstance(context, (int, float)) else None,
        "max_output": row.get("max_output_tokens") or None,
        "world_in_usd": per_million(row.get("input_cost_per_token")),
        "world_out_usd": per_million(row.get("output_cost_per_token")),
        "cache_read_usd": per_million(row.get("cache_read_input_token_cost")),
        "cache_write_usd": per_million(row.get("cache_creation_input_token_cost")),
        "image_usd": row.get("input_cost_per_image") or row.get("output_cost_per_image"),
        "audio_usd": row.get("input_cost_per_audio_per_second"),
        "caps": capabilities(row),
        "deprecated": bool(row.get("deprecation_date")),
    }


def merge_aa(card: dict[str, Any], model: dict[str, Any]) -> None:
    """Дописать качество и open-weights с artificialanalysis."""
    card["aa"] = {
        "name": model.get("name"),
        "creator": model.get("modelCreatorName"),
        "intelligence": _round(model.get("intelligenceIndex")),
        "estimated": bool(model.get("intelligenceIndexIsEstimated")),
        "reasoning": bool(model.get("isReasoning")),
        "open_weights": bool(model.get("isOpenWeights")),
        "price_in_usd": _round(model.get("price1mInputTokens"), 4),
        "price_out_usd": _round(model.get("price1mOutputTokens"), 4),
        "context": model.get("contextWindowTokens"),
        "param_class": model.get("paramClass"),
        "bench": {
            key: _round(model.get(key), 3)
            for key in (
                "gpqa",
                "hle",
                "scicode",
                "ifbench",
                "tau2",
                "terminalBench21",
                "terminalBench40",
                "mmmuPro",
                "omniscience",
            )
            if model.get(key) is not None
        },
    }
    card["caps"]["open_weights"] = bool(model.get("isOpenWeights")) or card["caps"]["open_weights"]
    if not card.get("context") and model.get("contextWindowTokens"):
        card["context"] = model["contextWindowTokens"]


def _round(value: Any, digits: int = 2) -> float | None:
    if isinstance(value, (int, float)):
        return round(float(value), digits)
    return None


# --------------------------------------------------------------------------- the catalogue
def build(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    litellm = litellm_rows()
    aa = artificial_analysis()
    aa_by_slug = {slug(m.get("slug") or m.get("name") or ""): m for m in aa}
    aa_by_name = {slug(m.get("name") or ""): m for m in aa}

    models: dict[str, dict[str, Any]] = {}
    # 1. всё, что знает LiteLLM: мировая цена и возможности
    for key, row in litellm.items():
        key_slug = slug(key)
        if not key_slug:
            continue
        card = litellm_card(key, row)
        card["id"] = key_slug
        match = aa_by_slug.get(key_slug) or aa_by_name.get(key_slug)
        if match:
            merge_aa(card, match)
        models[key_slug] = card

    # 1b. модели с оценкой качества, которых LiteLLM не знает: они тоже нужны в таблице,
    #     иначе «на рынке есть, у нас нет» не видно для половины рынка
    for model in aa:
        model_slug = slug(model.get("name") or model.get("slug") or "")
        if not model_slug or model_slug in models:
            continue
        card = _empty_card(model_slug, model.get("name") or model_slug, mode="chat")
        merge_aa(card, model)
        models[model_slug] = card

    # 2. наши поставщики: рубли за 1M (или за единицу), и модель, которой нет в LiteLLM
    ours: dict[str, dict[str, Any]] = {}
    if snapshot:
        for entry in snapshot.get("models") or []:
            our_slug = slug(entry.get("name") or entry.get("key") or "")
            if not our_slug:
                continue
            card = models.get(our_slug)
            if card is None:
                card = _empty_card(
                    our_slug, entry.get("name") or our_slug, mode=_mode_of(entry.get("unit"))
                )
                models[our_slug] = card
            prices = {}
            for offer in entry.get("offers") or []:
                provider = offer.get("provider")
                if provider:
                    prices[provider] = {
                        "rub": offer.get("rub"),
                        "unit": offer.get("unit"),
                        "native": offer.get("price"),
                        "currency": offer.get("currency"),
                    }
            if prices:
                card["ours"] = prices
                ours[our_slug] = card

    rows = list(models.values())
    for card in rows:
        card.setdefault("ours", {})
        card["available_here"] = bool(card["ours"])
        card["cheapest_rub"] = _cheapest(card["ours"])
        card["band"] = _band(card)
    rows.sort(key=lambda c: (not c["available_here"], -(c.get("aa", {}).get("intelligence") or 0)))
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": {
            "litellm": LITELLM_URL,
            "artificialanalysis": AA_URL,
            "ours": "console/providers/prices.json",
        },
        "counts": {
            "models": len(rows),
            "with_world_price": sum(1 for c in rows if c.get("world_in_usd")),
            "with_quality": sum(1 for c in rows if c.get("aa")),
            "available_here": sum(1 for c in rows if c["available_here"]),
        },
        "providers": _live_providers(rows),
        "world_providers": list(WORLD_PROVIDERS),
        "modalities": MODALITIES,
        "models": rows,
    }


def _empty_card(model_id: str, name: str, *, mode: str) -> dict[str, Any]:
    """Карточка модели, о которой известно только имя (и, может быть, режим)."""
    visual = mode in ("video_generation", "image_generation")
    audio = mode in ("audio_speech", "audio_transcription")
    return {
        "id": model_id,
        "name": name,
        "family": family_of(name),
        "provider": None,
        "mode": mode,
        "context": None,
        "max_output": None,
        "world_in_usd": None,
        "world_out_usd": None,
        "cache_read_usd": None,
        "cache_write_usd": None,
        "image_usd": None,
        "audio_usd": None,
        "caps": {
            "text": not visual and not audio,
            "code": not visual and not audio,
            "tools": False,
            "image": False,
            "audio_in": mode == "audio_transcription",
            "audio_out": mode == "audio_speech",
            "video": mode == "video_generation",
            "pdf": False,
            "reasoning": False,
            "open_weights": False,
            "caching": False,
            "structured": False,
            "computer_use": False,
            "web_search": False,
        },
        "deprecated": False,
    }


def _unit_of(ours: dict[str, dict[str, Any]]) -> str | None:
    """В каких единицах наши цены, если они одинаковые: токены, картинка, секунда видео.

    У одного продавца их бывает две (вход и выход за 1M токенов) и это разные числа -
    значит, единица живёт в каждом предложении, а не в модели."""
    units = {str(p.get("unit") or "") for p in ours.values()}
    units.discard("")
    return sorted(units)[0] if len(units) == 1 else ("разные" if units else None)


def _live_providers(rows: list[dict[str, Any]]) -> list[str]:
    """Поставщики в порядке OUR_PROVIDERS, но только те, у кого есть хотя бы одно предложение.

    Колонка без цен - это шум в таблице, а черновые поставщики (bothub, proxyapi, neuroapi)
    включаются отдельным флагом и в снимок по умолчанию не попадают."""
    present = {name for row in rows for name in (row.get("ours") or {})}
    ordered = [name for name in OUR_PROVIDERS if name in present]
    ordered += sorted(present - set(ordered))
    return ordered


def _mode_of(unit: Any) -> str:
    text = str(unit or "").lower()
    if "картин" in text:
        return "image_generation"
    if "видео" in text:
        return "video_generation"
    if "озвуч" in text:
        return "audio_speech"
    if "распозна" in text:
        return "audio_transcription"
    if "эмбед" in text:
        return "embedding"
    return "chat"


def _cheapest(ours: dict[str, dict[str, Any]]) -> float | None:
    values = [p["rub"] for p in ours.values() if isinstance(p.get("rub"), (int, float))]
    return round(min(values), 4) if values else None


def _band(card: dict[str, Any]) -> str | None:
    """Класс цены, чтобы фильтровать «дорого или дёшево» без точных чисел."""
    price = card.get("world_in_usd") or (card.get("aa") or {}).get("price_in_usd")
    if not isinstance(price, (int, float)) or price <= 0:
        return None
    if price < 0.2:
        return "cheap"
    if price < 1:
        return "budget"
    if price < 5:
        return "standard"
    if price < 15:
        return "premium"
    return "expensive"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="энциклопедия моделей для админки")
    parser.add_argument("--out", default=str(TARGET))
    parser.add_argument("--snapshot", default=str(SNAPSHOT))
    parser.add_argument("--check", action="store_true", help="не писать, только сравнить")
    args = parser.parse_args(argv)

    snapshot = None
    path = Path(args.snapshot)
    if path.exists():
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    else:
        print(f"нет снимка цен поставщиков: {path} (собираем без рублей)", file=sys.stderr)

    catalog = build(snapshot)
    text = json.dumps(catalog, ensure_ascii=False, indent=1) + "\n"
    target = Path(args.out)
    if args.check:
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        fresh = {k: v for k, v in catalog.items() if k != "generated_at"}
        try:
            existing = {k: v for k, v in json.loads(current or "{}").items() if k != "generated_at"}
        except ValueError:
            existing = None
        if existing != fresh:
            print(f"{target.name} устарел: запустить cloud/scripts/model_catalog.py", file=sys.stderr)
            return 1
        print(f"{target.name} в порядке")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    counts = catalog["counts"]
    print(
        f"записан {target}: {counts['models']} моделей, "
        f"{counts['with_world_price']} с мировой ценой, "
        f"{counts['with_quality']} с оценкой качества, "
        f"{counts['available_here']} у наших поставщиков "
        f"({len(text) / 1048576:.1f} МБ)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
