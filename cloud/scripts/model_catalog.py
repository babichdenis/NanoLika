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
#: models.dev — каталог от команды opencode: 232 провайдера, ~9000 моделей, у каждой тип
#: (chat/image/video/speech/embedding), модальности, контекст, цены и open_weights. Это
#: главный источник для «что это вообще за модель», LiteLLM — для цены за токен.
MODELSDEV_URL = "https://models.dev/api.json"
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


def models_dev() -> dict[str, dict[str, Any]]:
    """models.dev: одна запись на модель, собранная со всех провайдеров каталога.

    Ключ — нормализованное имя, поэтому `bytedance/seedance-2.5` из vercel и
    `doubao-seedance-2-5-260628` из volcengine сходятся в одну строку."""
    raw = json.loads(fetch(MODELSDEV_URL).decode("utf-8"))
    out: dict[str, dict[str, Any]] = {}
    for provider, data in (raw or {}).items():
        for model_id, model in (data.get("models") or {}).items():
            if not isinstance(model, dict):
                continue
            card = {
                "id": model_id,
                "name": model.get("name") or model_id,
                "provider": provider,
                "type": model.get("type"),
                "description": model.get("description"),
                "family": model.get("family"),
                "modalities": model.get("modalities") or {},
                "reasoning": bool(model.get("reasoning")),
                "tools": bool(model.get("tool_call")),
                "structured": bool(model.get("structured_output")),
                "attachment": bool(model.get("attachment")),
                "open_weights": bool(model.get("open_weights")),
                "knowledge": model.get("knowledge"),
                "released": model.get("release_date"),
                "updated": model.get("last_updated"),
                "limit": model.get("limit") or {},
                "cost": model.get("cost") or {},
            }
            keys = [k for k in slug_variants(model_id) + slug_variants(card["name"])
                    if any(ch.isalpha() for ch in k)]
            for key in keys:
                existing = out.get(key)
                # запись с ценой и контекстом полезнее пустой
                if existing is None or (
                    not existing["cost"] and card["cost"]
                ) or (not existing["limit"].get("context") and card["limit"].get("context")):
                    out[key] = card
    return out


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
def slug_variants(name: str) -> list[str]:
    """Все разумные ключи для одного имени: с префиксом продавца, без него и с вариациями.

    LiteLLM зовёт видеофраншизу по-своему (`fal_ai/bytedance/seedance-2.5/text-to-video`,
    `volcengine/doubao-seedance-2-5-260628`), а продавец — `seedance/2.5`. Без этого
    «seedance» оставался бы без цены и без режима, хотя всё это у LiteLLM есть."""
    text = (name or "").strip().lower()
    out: list[str] = []

    def push(value: str) -> None:
        key = slug(value)
        if key and key not in out:
            out.append(key)

    push(text)
    # без продавца: fal_ai/bytedance/seedance-2.5/... → seedance-2.5
    parts = [p for p in text.replace(":", "/").split("/") if p]
    for i in range(len(parts)):
        push("/".join(parts[i:]))
        push(parts[i])
    # «doubao-seedance-2-5-260628» → «seedance-2-5», «seedance 2.5» → «seedance-2.5»
    joined = "-".join(parts[-2:]) if len(parts) >= 2 else text
    push(joined)
    push(re.sub(r"-\d{6,}$", "", joined))          # даты вида 260628
    push(re.sub(r"\b(\d)-(\d)\b", r"\1.\2", joined))  # seedance-2-5 → seedance-2.5
    # «wan/2.5» → «wan-2-5»: продавцы пишут семейство и версию через слэш. Только когда
    # частей ровно две - иначе склейка плодит десятки ложных ключей и дублей в каталоге.
    if len(parts) == 2:
        squashed = parts[0] + "-" + parts[1].replace(".", "-")
        push(squashed)
        push(squashed.replace("-", ""))
    return out


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


def _has_price(row: dict[str, Any]) -> bool:
    """Есть ли у строки LiteLLM хоть какая-то цена: за токены, секунду, картинку."""
    for field in (
        "input_cost_per_token",
        "output_cost_per_token",
        "output_cost_per_second",
        "output_cost_per_image",
        "input_cost_per_image",
        "output_cost_per_character",
    ):
        value = row.get(field)
        if isinstance(value, (int, float)) and value > 0:
            return True
    return False


def _unit_price(row: dict[str, Any], mode: str) -> dict[str, Any] | None:
    """Цена за единицу у медиа: секунда видео, картинка, символ озвучки.

    В LiteLLM это разные поля, и у видео их несколько по разрешениям (480p/720p/1080p):
    берём то, что есть, и называем единицу, иначе в таблице остаётся прочерк."""
    for field, unit in (
        ("output_cost_per_second_720p", "сек 720p"),
        ("output_cost_per_second_1080p", "сек 1080p"),
        ("output_cost_per_second_480p", "сек 480p"),
        ("output_cost_per_second", "сек"),
        ("output_cost_per_image", "картинка"),
        ("input_cost_per_image", "картинка"),
        ("output_cost_per_character", "1K символов"),
        ("input_cost_per_audio_per_second", "сек звука"),
        ("output_cost_per_audio_token", "токен звука"),
    ):
        value = row.get(field)
        if isinstance(value, (int, float)) and value > 0:
            amount = float(value) * 1000 if field == "output_cost_per_character" else float(value)
            return {"usd": round(amount, 6), "unit": unit, "mode": mode}
    return None


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
        # у видео и картинок цена не за токены: секунда генерации, картинка, символ
        "unit_usd": _unit_price(row, mode),
        "image_usd": row.get("input_cost_per_image") or row.get("output_cost_per_image"),
        "audio_usd": row.get("input_cost_per_audio_per_second"),
        "caps": capabilities(row),
        "modalities": {
            "in": [str(m) for m in (row.get("supported_modalities") or [])],
            "out": [str(m) for m in (row.get("supported_output_modalities") or [])],
        },
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


DEV_MODES = {
    "chat": "chat",
    "image": "image_generation",
    "video": "video_generation",
    "speech": "audio_speech",
    "transcription": "audio_transcription",
    "embedding": "embedding",
    "reranking": "rerank",
    "realtime": "realtime",
}


def merge_dev(card: dict[str, Any], match: dict[str, Any]) -> None:
    """Дописать/уточнить карточку из models.dev.

    Источник не спорит с LiteLLM о цене за токен (там она точнее), но именно он знает,
    что модель вообще за тип, какие у неё модальности, контекст и открытые веса."""
    kind = DEV_MODES.get(str(match.get("type") or ""), None)
    if kind and card.get("mode") in (None, "chat"):
        card["mode"] = kind
    caps = card.setdefault("caps", {})
    mods = match.get("modalities") or {}
    inputs = [str(m) for m in (mods.get("input") or [])]
    outputs = [str(m) for m in (mods.get("output") or [])]
    # модальности копим, а «умеет» считаем один раз в конце: иначе «текст» на входе
    # (промпт к видео) превращается в умение «текст», а «код» прилипает от слияния
    card.setdefault("modalities", {"in": [], "out": []})
    card["modalities"]["in"] = sorted({*card["modalities"].get("in", []), *inputs})
    card["modalities"]["out"] = sorted({*card["modalities"].get("out", []), *outputs})
    caps["pdf"] = caps.get("pdf") or "pdf" in inputs
    caps["reasoning"] = bool(match.get("reasoning")) or caps.get("reasoning", False)
    caps["tools"] = bool(match.get("tools")) or caps.get("tools", False)
    caps["structured"] = bool(match.get("structured")) or caps.get("structured", False)
    caps["open_weights"] = bool(match.get("open_weights")) or caps.get("open_weights", False)
    limits = match.get("limit") or {}
    if not card.get("context") and isinstance(limits.get("context"), (int, float)):
        card["context"] = int(limits["context"])
    if not card.get("max_output") and isinstance(limits.get("output"), (int, float)):
        card["max_output"] = int(limits["output"])
    # у медиа models.dev цену за токен не даёт: тип `video`/`image` уже полезен сам по себе
    if kind in ("video_generation", "image_generation", "audio_speech") and not card.get("unit_usd"):
        costs = match.get("cost") or {}
        for field, unit in (("input", "единица"), ("output", "единица")):
            value = costs.get(field)
            if isinstance(value, (int, float)) and value > 0:
                card["unit_usd"] = {"usd": float(value), "unit": unit, "mode": kind, "source": "models.dev"}
                break
    cost = match.get("cost") or {}
    # цены models.dev — за 1M токенов, как и наши мировые
    if not card.get("world_in_usd") and isinstance(cost.get("input"), (int, float)):
        card["world_in_usd"] = round(float(cost["input"]), 4)
    if not card.get("world_out_usd") and isinstance(cost.get("output"), (int, float)):
        card["world_out_usd"] = round(float(cost["output"]), 4)
    if not card.get("cache_read_usd") and isinstance(cost.get("cache_read"), (int, float)):
        card["cache_read_usd"] = round(float(cost["cache_read"]), 4)
    card["dev"] = {
        "type": match.get("type"),
        "provider": match.get("provider"),
        "open_weights": bool(match.get("open_weights")),
        "released": match.get("released"),
        "updated": match.get("updated"),
        "knowledge": match.get("knowledge"),
        "description": (match.get("description") or "")[:240] or None,
    }


#: Что модель умеет по своему роду занятий: «текст» у видео — это только промпт, а не
#: способность читать документы, поэтому режим решает больше, чем модальности.
GENERATIVE = {"video_generation", "image_generation", "audio_speech"}
PASSIVE = {"embedding", "rerank", "audio_transcription"}


def finalize_caps(card: dict[str, Any]) -> None:
    """Свести «умеет» из режима и модальностей — один раз, после всех слияний.

    Правила простые и проверяемые:
    * генератор (видео, картинки, озвучка) не «умеет текст» и не «умеет код»: текст у него
      только на вход как описание, а код он не пишет;
    * распознавание (audio_transcription) принимает звук и отдаёт текст — это текст, но не код;
    * эмбеддинги и реранк не чат: ни инструментов, ни кода;
    * «код» — это инструменты/чат, который пишет код, а не любая текстовая модель.
    """
    mode = str(card.get("mode") or "chat")
    caps = card.setdefault("caps", {})
    mods = card.get("modalities") or {}
    inputs = set(mods.get("in") or [])
    outputs = set(mods.get("out") or [])
    if mode in GENERATIVE:
        caps["text"] = False
        caps["code"] = False
        caps["tools"] = False
        caps["structured"] = False
    elif mode in PASSIVE:
        caps["text"] = mode == "audio_transcription"
        caps["code"] = False
        caps["tools"] = False
        caps["structured"] = False
    else:
        # чат и responses: текст есть по определению; код — если есть инструменты или это
        # разговорная модель (видео и картинки сюда не попадают, они выше)
        caps["text"] = True
        caps["code"] = bool(caps.get("tools")) or mode in ("chat", "responses")
    # модальности: «картинки» — это вход или выход картинок, а не «модель про картинки»
    if "image" in inputs:
        caps["image"] = True
    if "audio" in inputs:
        caps["audio_in"] = True
    if "audio" in outputs:
        caps["audio_out"] = True
    if "video" in inputs or "video" in outputs:
        caps["video"] = True
    if "pdf" in inputs:
        caps["pdf"] = True


def _round(value: Any, digits: int = 2) -> float | None:
    if isinstance(value, (int, float)):
        return round(float(value), digits)
    return None


# --------------------------------------------------------------------------- the catalogue
def build(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    litellm = litellm_rows()
    aa = artificial_analysis()
    aa_by_slug: dict[str, dict[str, Any]] = {}
    for model in aa:
        for key in slug_variants(model.get("slug") or model.get("name") or ""):
            aa_by_slug.setdefault(key, model)
    aa_by_name = aa_by_slug

    lit_by_slug: dict[str, tuple[str, dict[str, Any]]] = {}
    for key, row in litellm.items():
        for variant in slug_variants(key):
            # у одного ключа бывает несколько строк: первая с ценой важнее
            current = lit_by_slug.get(variant)
            if current is None or (not _has_price(current[1]) and _has_price(row)):
                lit_by_slug[variant] = (key, row)

    dev = models_dev()

    models: dict[str, dict[str, Any]] = {}
    # 1. всё, что знает LiteLLM: мировая цена и возможности. Ключ — самое короткое имя,
    #    чтобы `seedance-2.5` и `fal_ai/bytedance/seedance-2.5/text-to-video` были одной строкой
    for key, row in litellm.items():
        variants = slug_variants(key)
        if not variants:
            continue
        # самое короткое имя, но не обрубок: ключ должен содержать буквы, а не только версию
        usable = [v for v in variants if any(ch.isalpha() for ch in v)] or variants
        key_slug = min(usable, key=len)
        existing = models.get(key_slug)
        if existing is not None and (_has_price(existing) or not _has_price(row)):
            continue
        card = litellm_card(key, row)
        card["id"] = key_slug
        match = aa_by_slug.get(key_slug)
        if match:
            merge_aa(card, match)
        models[key_slug] = card

    # 1c. models.dev: тип модели, модальности, контекст, цены, open_weights. Заполняет то,
    #     чего в LiteLLM нет вовсе (видео, озвучка, распознавание) и не затирает то, что есть
    for key, card in list(models.items()):
        match = dev.get(key)
        if match:
            merge_dev(card, match)
    for key, match in dev.items():
        if key in models:
            continue
        card = _empty_card(key, match["name"], mode="chat")
        merge_dev(card, match)
        models[key] = card

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
            our_name = entry.get("name") or entry.get("key") or ""
            our_slug = slug(our_name)
            if not our_slug:
                continue
            # `wan/2.5` в LiteLLM зовётся `dashscope/wan2.5-t2v`: пробуем все варианты
            card = None
            for variant in slug_variants(our_name):
                card = models.get(variant)
                if card is not None:
                    our_slug = variant
                    break
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
        finalize_caps(card)
        card.setdefault("ours", {})
        card["available_here"] = bool(card["ours"])
        # есть ли что показать, кроме названия: хоть одна цена мира, качество или медиа-цена
        card["has_data"] = bool(
            card.get("world_in_usd")
            or card.get("world_out_usd")
            or card.get("unit_usd")
            or card.get("aa")
            or card.get("dev")
            or card.get("context")
        )
        card["cheapest_rub"] = _cheapest(card["ours"])
        card["band"] = _band(card)
    rows.sort(key=lambda c: (not c["available_here"], -(c.get("aa", {}).get("intelligence") or 0)))
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": {
            "litellm": LITELLM_URL,
            "artificialanalysis": AA_URL,
            "modelsdev": MODELSDEV_URL,
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
        "unit_usd": None,
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
