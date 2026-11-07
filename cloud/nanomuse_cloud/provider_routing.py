"""Request-priced routing from structured supplier catalogs, never scraped minima."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import time
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .deepseek_pricing import PRICE_URL as DEEPSEEK_PRICE_URL
from .deepseek_pricing import catalog as deepseek_catalog
from .providers import catalogue

log = logging.getLogger(__name__)


def _catalog_entry(name: str) -> dict[str, Any]:
    """One provider as the catalogue describes it; an empty dict when it is absent."""
    for entry in catalogue().get("providers", []):
        if entry.get("id") == name:
            return entry
    return {}


def _has_catalog(name: str) -> bool:
    return bool(_catalog_entry(name).get("catalog"))


FLASH_FAMILY = "deepseek/deepseek-v4.1-flash"
ALIASES = {
    # Provider revision IDs are normalized to one customer-facing Flash family.
    "deepseek-flash": FLASH_FAMILY,
    "deepseek-v4.1-flash": FLASH_FAMILY,
    "deepseek/deepseek-v4.1-flash": FLASH_FAMILY,
    # Older Flash generations are lower-priced implementations of the same
    # customer-facing product. Feature checks below still keep vision/tool routes
    # on providers and generations that actually support those capabilities.
    "deepseek-v4-flash": FLASH_FAMILY,
    "deepseek/deepseek-v4-flash": FLASH_FAMILY,
    "deepseek-v4-flash-vision-exp": FLASH_FAMILY,
    "deepseek/deepseek-v4-flash-vision-exp": FLASH_FAMILY,
    "deepseek-v4-flash-0731": FLASH_FAMILY,
    "deepseek/deepseek-v4-flash-0731": FLASH_FAMILY,
    # OpenRouter's tilde-prefixed aliases are dynamic Flash routes. Keep them in
    # the same internal family so their live prices and availability are visible.
    "~deepseek/deepseek-flash-latest": FLASH_FAMILY,
    "~deepseek/deepseek-v4-flash-latest": FLASH_FAMILY,
    "deepseek/deepseek-v4.1-flash-1017": FLASH_FAMILY,
    "deepseek-v4.1-flash-1017": FLASH_FAMILY,
    # Yandex models are named differently by RouterAI and Polza; one customer
    # position must route to both, not appear twice in the menu.
    "yandex/yandexgpt-5-lite": "yandex/gpt-lite-5",
    "yandex/yandexgpt-5-pro": "yandex/gpt-pro-5",
    "yandex/yandexgpt-5.1-pro": "yandex/gpt-pro-5.1",
}
VENDORS = {"deepseek", "openai", "anthropic", "google", "qwen", "z-ai", "meta-llama"}
# Names whose catalogue entry carries a `catalog` descriptor: the relay knows how to
# read their model list. Adding a provider is a catalogue edit, not a code edit.
ROUTING_CATALOG_PROVIDERS = frozenset(entry["id"] for entry in catalogue().get("providers", []) if entry.get("catalog"))


def identity(model: str) -> str:
    return ALIASES.get(model.lower(), model)


# Only the customer-facing alias routes across the DeepSeek Flash family. A
# version-specific spec (0423, 0731, 1017, vision-exp, latest) must use its own
# offers: otherwise the admin says 1.49 while the client asked for 0423.
FAMILY_ANCHORS = {
    "deepseekflash",
    "yandexgptlite5",
    "yandexgptpro5",
    "yandexgptpro51",
}


def model_key(value: str) -> str:
    """Bare model id for exact matching: `deepseek/deepseek-v4-flash` → `deepseekv4flash`."""
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower().rsplit("/", 1)[-1])


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def proxy_catalog(html: str) -> dict:
    """Read explicit numeric RUB/M breakdowns from the supplier's public catalog.

    This is a guarded website schema, not a guaranteed pricing API. Fail closed
    when the schema changes; never infer prices from formatted 'from' labels.
    """
    for match in re.finditer(r"self\.__next_f\.push\((\[.*?\])\)</script>", html):
        packet = json.loads(match.group(1))
        if len(packet) < 2 or not isinstance(packet[1], str):
            continue
        text = packet[1]
        match_rows = re.search(r'"rows"\s*:\s*(\[)', text)
        if match_rows is None:
            continue
        rows, _ = json.JSONDecoder().raw_decode(text[match_rows.start(1) :])
        if not isinstance(rows, list):
            continue
        result = []
        for row in rows:
            if row.get("category") != "text" or not isinstance(row.get("key"), str):
                continue
            prices = {}
            unsupported = False
            for price in row.get("breakdown") or []:
                if price.get("key") not in {"input_tokens", "output_tokens", "cache_read_input_tokens"}:
                    unsupported = True  # Extra billing dimensions need explicit handling.
                if price.get("hint") == "за 1М токенов":
                    prices[price.get("key")] = number(price.get("price"))
            if unsupported or prices.get("input_tokens") is None or prices.get("output_tokens") is None:
                continue
            result.append(
                dict(
                    id=row["key"],
                    pricing=dict(currency="RUB", **prices),
                    context_length=row.get("contextTokens") or 0,
                    supported_parameters=(["tools"] if "functions" in row.get("capabilities", []) else []),
                    architecture={"input_modalities": row.get("inputModalities") or ["text"]},
                )
            )
        if result:
            return {"data": result}
    raise ValueError("ProxyAPI public pricing schema unavailable")


def estimate_prompt(body: dict) -> int:
    """Text and tool schemas plus an image allowance, not base64 character length."""

    def measure(value: Any) -> tuple[int, int]:
        if isinstance(value, str):
            return (0, 1) if value.startswith("data:image/") else (len(value), 0)
        if isinstance(value, dict):
            kind = value.get("type")
            if isinstance(kind, str) and kind in {"image_url", "input_image", "image"}:
                return 0, 1
            parts = [measure(v) for v in value.values()]
        elif isinstance(value, list):
            parts = [measure(v) for v in value]
        else:
            return 0, 0
        return sum(p[0] for p in parts), sum(p[1] for p in parts)

    chars, images = measure(body.get("messages", []))
    tools = body.get("tools") or body.get("functions")
    if tools:
        chars += len(json.dumps(tools, ensure_ascii=False))
    return max(1, math.ceil(chars / 3) + images * 1024)


@dataclass(frozen=True)
class Offer:
    provider: str
    model: str
    input_rub: float
    output_rub: float
    cached_rub: float | None
    context: int
    output_limit: int
    parameters: tuple[str, ...]
    modalities: tuple[str, ...]
    updated: float
    endpoint: str = ""
    overrides: tuple[dict, ...] = ()
    canonical_model: str = ""
    request_rub: float = 0.0
    output_modalities: tuple[str, ...] = ()
    source_url: str = ""
    source_currency: str = "RUB"
    fx_rub_per_unit: float | None = None
    valid_until: int = 0

    def rates(self, at: float | None = None, prompt_tokens: int | None = None) -> tuple[float, float, float | None]:
        now = datetime.fromtimestamp(time.time() if at is None else at, UTC)
        minute = now.hour * 100 + now.minute
        day = now.strftime("%A").lower()
        for rule in self.overrides:
            # Длинный тариф (min_prompt_tokens) включается только когда промпт дорос
            # до порога. Раньше он применялся всегда и завышал цену в два раза.
            minimum = rule.get("min_prompt_tokens")
            if minimum is not None and (prompt_tokens is None or prompt_tokens < int(minimum)):
                continue
            if rule.get("utc_dates") and now.date().isoformat() not in rule["utc_dates"]:
                continue
            if rule.get("utc_days") and day not in rule["utc_days"]:
                continue
            start, end = int(rule.get("utc_start", 0)), int(rule.get("utc_end", 0))
            matches = start == end or (start <= minute < end if start < end else minute >= start or minute < end)
            if matches:
                return rule["input_rub"], rule["output_rub"], rule.get("cached_rub")
        return self.input_rub, self.output_rub, self.cached_rub

    def estimate(self, prompt: int, output: int, cached: int = 0) -> float:
        inp, out, cache = self.rates(prompt_tokens=prompt)
        cached = min(max(cached, 0), max(prompt, 0)) if cache is not None else 0
        return self.request_rub + ((prompt - cached) * inp + cached * (cache or 0) + output * out) / 1_000_000


@dataclass(frozen=True)
class Route:
    provider: str
    url: str
    headers: dict[str, str]
    body: dict
    offer: Offer | None = None
    routing_eligible: bool = True
    exclusion_reason: str = ""
    # Other ids of the same model family at the same supplier that claim pictures:
    # when this id refuses one, the turn stays with the supplier instead of leaving it.
    siblings: tuple[Offer, ...] = ()


class ProviderRouter:
    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.offers: dict[str, list[Offer]] = {}
        self.errors: dict[str, str] = {}
        self.balances: dict[str, dict] = {}
        self.refreshed = 0.0
        # Use a stable accounting basis. The operator may change it explicitly, but
        # automatic daily FX movement must not rewrite historical provider costs.
        self.usd_rub = float(os.getenv("CLOUD_ROUTING_USD_RUB", "90"))
        self.usd_fee = float(os.getenv("CLOUD_ROUTING_USD_FEE_PCT", "5.5"))
        self.refresh_seconds = max(60, int(os.getenv("CLOUD_ROUTING_REFRESH_SECONDS", "1800")))
        self.max_age = max(self.refresh_seconds, int(os.getenv("CLOUD_ROUTING_MAX_AGE_SECONDS", "86400")))
        self.expected_output = max(1, int(os.getenv("CLOUD_ROUTING_EXPECTED_OUTPUT", "512")))
        self.minimum_balance_usd = max(0.0, float(os.getenv("CLOUD_ROUTING_MIN_BALANCE_USD", "1")))
        self.minimum_balance_rub = max(0.0, float(os.getenv("CLOUD_ROUTING_MIN_BALANCE_RUB", "90")))
        self.allow_quantized = os.getenv("CLOUD_ROUTING_ALLOW_QUANTIZED", "0") == "1"
        self.allow_unpriced = os.getenv("CLOUD_ROUTING_ALLOW_UNPRICED_FALLBACK", "0") == "1"
        self.enabled = os.getenv("CLOUD_AUTO_ROUTING", "1") == "1"
        self.price_mismatch_pct = max(0.0, float(os.getenv("CLOUD_ROUTING_PRICE_MISMATCH_PCT", "25")))
        self.price_mismatch_min_rub = max(0.0, float(os.getenv("CLOUD_ROUTING_PRICE_MISMATCH_MIN_RUB", "0.02")))
        self.quarantine_seconds = max(self.refresh_seconds, int(os.getenv("CLOUD_ROUTING_PRICE_QUARANTINE_SECONDS", "1800")))
        self.quarantined: dict[tuple[str, str], float] = {}
        # What a supplier's catalog says about pictures is a claim; what it answers is a
        # fact. `vision_confirmed` records a route that took a picture (even when its
        # catalog said text-only), `vision_blocked` a route that refused one while another
        # route with the same body answered — the strike count grows the pause.
        self.vision_confirmed: dict[str, float] = {}
        self.vision_blocked: dict[str, float] = {}
        self.vision_strikes: dict[str, int] = {}
        self.provider_blocked_until: dict[str, float] = {}
        self.price_alerted: dict[tuple[str, str], float] = {}
        self.lock = asyncio.Lock()
        path = os.getenv("CLOUD_ROUTING_CACHE")
        self.path = Path(path) if path else None
        if self.path:
            try:
                raw = json.loads(self.path.read_text())
                for row in raw.get("offers", []):
                    row["parameters"] = tuple(row["parameters"])
                    row["modalities"] = tuple(row["modalities"])
                    row["output_modalities"] = tuple(row.get("output_modalities", ()))
                    row["overrides"] = tuple(row.get("overrides", []))
                    offer = Offer(**row)
                    self.offers.setdefault(offer.provider, []).append(offer)
                balances = raw.get("balances", {})
                if isinstance(balances, dict):
                    self.balances = {str(name): value for name, value in balances.items() if isinstance(value, dict)}
                blocked = raw.get("provider_blocked_until", {})
                if isinstance(blocked, dict):
                    now = time.time()
                    self.provider_blocked_until = {
                        str(name): until for name, value in blocked.items() if (until := number(value)) is not None and until > now
                    }
                blind = raw.get("vision_blocked", {})
                if isinstance(blind, dict):
                    now = time.time()
                    self.vision_blocked = {
                        str(key): until for key, value in blind.items() if (until := number(value)) is not None and until > now
                    }
                seen = raw.get("vision_confirmed", {})
                if isinstance(seen, dict):
                    now = time.time()
                    self.vision_confirmed = {
                        str(key): until for key, value in seen.items() if (until := number(value)) is not None and until > now
                    }
                strikes = raw.get("vision_strikes", {})
                if isinstance(strikes, dict):
                    self.vision_strikes = {str(key): int(value) for key, value in strikes.items() if number(value) is not None}
            except (OSError, ValueError, TypeError, KeyError):
                pass

    def _offer(self, provider: str, model: str) -> Offer | None:
        for candidate in self.offers.get(provider, []):
            if candidate.model == model:
                return candidate
        return None

    def vision_siblings(self, provider: str, offer: Offer, now: float) -> tuple[Offer, ...]:
        """Other ids of the same family at this supplier that can take a picture.

        Suppliers sell one model family under several ids and only some of them see.
        When the id a customer asked for refuses the picture, the turn stays with the
        same supplier on a sibling id instead of the supplier being dropped.
        """
        family = identity(offer.canonical_model or offer.model)
        rows = []
        for other in self.offers.get(provider, []):
            if other.model == offer.model:
                continue
            if identity(other.canonical_model or other.model) != family:
                continue
            pair = offer_key(provider, other.model)
            if "image" not in other.modalities and self.vision_confirmed.get(pair, 0) <= now:
                continue
            if self.vision_blocked.get(pair, 0) > now:
                continue
            if self.quarantined.get((provider, other.model.lower()), 0) > now:
                continue
            if other.valid_until and now >= other.valid_until:
                continue
            if now - other.updated > self.max_age:
                continue
            if not self.allow_quantized and any(q in other.endpoint.lower() for q in ("fp4", "int4", "q4")):
                continue
            rows.append((other.input_rub, other.model, other))
        rows.sort(key=lambda row: (row[0], row[1]))
        return tuple(row[2] for row in rows)

    def below_balance_floor(self, balance: dict) -> bool:
        amount = number(balance.get("amount"))
        if amount is None:
            return False
        floor = self.minimum_balance_usd if str(balance.get("currency", "RUB")).upper() == "USD" else self.minimum_balance_rub
        return amount <= floor

    def parse(self, provider: str, data: Any, at: float) -> list[Offer]:
        """Only supported schemas with explicit currency and separate token units."""
        entry = _catalog_entry(provider)
        fmt = str((entry.get("catalog") or {}).get("format") or "")
        if fmt == "aitunnel":
            rows = [dict(row, id=model) for model, row in data.items() if isinstance(row, dict)]
        else:
            rows = data.get("data", [])
        result = []
        for row in rows:
            model = row.get("id")
            if not isinstance(model, str) or not model or model.endswith(":free"):
                continue  # Free capacity belongs to the explicit trial, never paid fallback.
            endpoint = row.get("top_provider") or {}
            price = row.get("pricing") or endpoint.get("pricing") or {}
            currency = price.get("currency")
            overrides = []
            request_rub = 0.0
            if fmt == "polza":
                if currency != "RUB" or row.get("type") != "chat":
                    continue
                inp = number(price.get("prompt_per_million"))
                out = number(price.get("completion_per_million"))
                cache = number(price.get("input_cache_read_per_million"))
                parameters = endpoint.get("supported_parameters") or []
            elif fmt == "proxyapi":
                if currency != "RUB":
                    continue
                inp, out = number(price.get("input_tokens")), number(price.get("output_tokens"))
                cache = number(price.get("cache_read_input_tokens"))
                parameters = row.get("supported_parameters") or []
            elif fmt in {"openrouter", "routerai"}:
                factor = 1_000_000.0
                if fmt == "openrouter":
                    factor *= self.usd_rub * (1 + self.usd_fee / 100)

                def rate(field: str, price: dict = price, factor: float = factor) -> float | None:
                    value = number(price.get(field))
                    return value * factor if value is not None else None

                inp, out, cache = rate("prompt"), rate("completion"), rate("input_cache_read")
                request_rub = (number(price.get("request")) or 0) * factor / 1_000_000
                for rule in price.get("overrides", []):
                    ri, ro, rc = number(rule.get("prompt")), number(rule.get("completion")), number(rule.get("input_cache_read"))
                    if ri is not None and ro is not None:
                        overrides.append(
                            dict(rule, input_rub=ri * factor, output_rub=ro * factor, cached_rub=rc * factor if rc is not None else None)
                        )
                parameters = row.get("supported_parameters") or []
            elif fmt == "deepseek":
                inp, out = number(price.get("input_rub")), number(price.get("output_rub"))
                cache = number(price.get("cached_rub"))
                overrides = price.get("overrides") or []
                parameters = row.get("supported_parameters") or []
            elif fmt == "beatapi":
                # `/v1/models` carries no prices: the descriptor keeps the card prices
                # (Inspect is free), in USD per 1M tokens.
                card = (entry.get("prices") or {}).get(model) or {}
                factor = self.usd_rub  # the card is already USD per 1M tokens
                inp = number(card.get("in"))
                out = number(card.get("out"))
                inp = inp * factor if inp is not None else None
                out = out * factor if out is not None else None
                cache = number(card.get("cache"))
                parameters = ["tools", "tool_choice"] if card.get("tools") else []
            elif fmt == "aitunnel":
                inp, out = number(row.get("prompt_cost")), number(row.get("completion_cost"))
                discount = number(row.get("cache_discount"))
                cache = inp * (1 - discount) if inp is not None and discount is not None and discount <= 1 else None
                parameters = list(row.get("supported_parameters") or [])
                tool_models = entry.get("tool_models") or []
                if model.lower() in tool_models:
                    parameters = list(dict.fromkeys([*parameters, "tools", "tool_choice"]))
            else:
                continue  # An unpriced model list is not a supplier offer.
            if inp is None or out is None or inp == out == 0:
                continue
            if fmt == "beatapi":
                # The model list has no architecture and no prices: both come from the card.
                card = (entry.get("prices") or {}).get(model) or {}
                modalities = ["text", "image"] if card.get("vision") else ["text"]
                outputs = ["text"]
                row = {
                    **row,
                    "context_length": card.get("context") or row.get("context_length") or 0,
                    "max_output": card.get("max_out") or row.get("max_output") or 0,
                }
            else:
                architecture = row.get("architecture") or row.get("modalities") or {}
                modalities = architecture.get("input_modalities") or architecture.get("input") or ["text"]
                outputs = architecture.get("output_modalities") or architecture.get("output") or ["text"]
            if "text" not in outputs:
                continue
            vendor = str(row.get("provider") or "")
            canonical = f"{vendor}/{model}" if fmt == "aitunnel" and "/" not in model and vendor in VENDORS else model
            result.append(
                Offer(
                    provider,
                    model,
                    inp,
                    out,
                    cache,
                    int(row.get("context_length") or row.get("context_size") or 0),
                    int(row.get("max_completion_tokens") or endpoint.get("max_completion_tokens") or row.get("max_output") or 0),
                    tuple(parameters),
                    tuple(modalities),
                    at,
                    str(endpoint.get("name") or ""),
                    tuple(overrides),
                    identity(canonical),
                    request_rub,
                    tuple(outputs),
                    source_url=str(row.get("source_url") or ""),
                    source_currency=str(row.get("source_currency") or ("USD" if fmt in {"openrouter", "beatapi"} else "RUB")),
                    fx_rub_per_unit=number(row.get("fx_rub_per_unit")) or (self.usd_rub if fmt in {"openrouter", "beatapi"} else None),
                    valid_until=int(row.get("valid_until") or 0),
                )
            )
        return result

    async def refresh(self, http: httpx.AsyncClient) -> None:
        if not self.enabled:
            return
        async with self.lock:

            async def one(name: str, provider: Any) -> None:
                if not provider.key() or name not in ROUTING_CATALOG_PROVIDERS:
                    return
                descriptor = _catalog_entry(name)
                catalog = descriptor.get("catalog") or {}
                fmt = str(catalog.get("format") or "")
                url = str(catalog.get("url") or (provider.base.rstrip("/") + str(catalog.get("path") or "/v1/models")))
                headers = {} if catalog.get("auth") == "none" else {"Authorization": "Bearer " + provider.key()}
                try:
                    response = await http.get(url, headers=headers, timeout=25)
                    response.raise_for_status()
                    data = proxy_catalog(response.text) if fmt == "proxyapi" else response.json()
                    if fmt == "deepseek":
                        price_url = str(catalog.get("price_url") or DEEPSEEK_PRICE_URL)
                        price_response = await http.get(price_url, timeout=25)
                        price_response.raise_for_status()
                        data = deepseek_catalog(price_response.text, data, time.time(), self.usd_rub)
                    if fmt == "proxyapi":
                        # A public price is eligible only when this key can see the model.
                        allowed_path = str(catalog.get("allowed_models_path") or "/v1/models")
                        catalog_response = await http.get(
                            provider.base.rstrip("/") + allowed_path,
                            headers={"Authorization": "Bearer " + provider.key()},
                            timeout=25,
                        )
                        catalog_response.raise_for_status()
                        allowed = {row["id"] for row in catalog_response.json().get("data", [])}
                        data["data"] = [row for row in data["data"] if row["id"] in allowed]
                    offers = self.parse(name, data, time.time())
                    offers = [replace(offer, source_url=offer.source_url or url) for offer in offers]
                    if not offers:
                        raise ValueError("no supported token quotes")
                    self.offers[name] = offers
                    self.errors.pop(name, None)
                except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError) as exc:
                    # Do not print exception text, HTTP errors can contain private URLs.
                    self.errors[name] = type(exc).__name__
                    log.warning("routing catalog %s: %s", name, type(exc).__name__)

            await asyncio.gather(*(one(k, v) for k, v in self.settings.providers.items()))
            self.refreshed = time.time()
            await self.refresh_balances(http)
            self.persist()

    async def refresh_balances(self, http: httpx.AsyncClient) -> None:
        async def one(name: str, provider: Any) -> None:
            descriptor = _catalog_entry(name)
            balance = descriptor.get("balance") or {}
            path = str(balance.get("path") or "")
            if not path or not provider.key():
                return
            fmt = str(balance.get("format") or "")
            currency = str(balance.get("currency") or "RUB")
            entry = {"amount": None, "currency": currency, "checked": time.time(), "error": None}
            base = str(balance.get("base") or provider.base.rstrip("/"))
            key_env = balance.get("key_env")
            key = os.getenv(str(key_env), provider.key()) if key_env else provider.key()
            try:
                response = await http.get(base + path, headers={"Authorization": "Bearer " + key}, timeout=15)
                response.raise_for_status()
                data = response.json()
                if fmt == "deepseek":
                    # {"is_available": false, "balance_infos": [{"currency": "USD", "total_balance": "-0.00"}, …]}
                    infos = data.get("balance_infos") or []
                    for info in infos:
                        if str(info.get("currency")).upper() == "USD":
                            amount = number(info.get("total_balance"))
                            break
                    entry["available"] = bool(data.get("is_available"))
                    entry["cny"] = next(
                        (number(i.get("total_balance")) for i in infos if str(i.get("currency")).upper() == "CNY"),
                        None,
                    )
                elif fmt == "openrouter":
                    data = data.get("data", {})
                    total, used = number(data.get("total_credits")), number(data.get("total_usage"))
                    amount = total - used if total is not None and used is not None else None
                elif fmt == "routerai":
                    amount = number(data.get("data", {}).get("credits"))
                elif fmt == "polza":
                    amount = number(data.get("available"))
                elif fmt == "beatapi":
                    amount = number((data.get("data") or {}).get("credit_balance"))
                else:
                    amount = number(data.get("balance"))
                    budget = data.get("budget")
                    if fmt == "aitunnel" and number(budget) is not None:
                        entry["key_budget"] = number(budget)
                        amount = min(amount, number(budget)) if amount is not None else None
                    if fmt == "proxyapi" and isinstance(budget, dict):
                        entry["scope"] = "key_budget"
                if amount is None:
                    raise ValueError("unknown balance schema")
                entry["amount"] = amount
                entry["rub_estimate"] = amount * self.usd_rub if currency == "USD" else amount
            except httpx.HTTPStatusError as exc:
                entry["error"] = f"HTTP {exc.response.status_code}"
            except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
                entry["error"] = type(exc).__name__
            self.balances[name] = entry
            amount = number(entry.get("amount"))
            floor = self.minimum_balance_usd if currency == "USD" else self.minimum_balance_rub
            if entry.get("error") is None and amount is not None and amount > floor:
                # A fresh top-up above the operating floor immediately re-enables
                # this provider. Route ranking then puts it first if it is cheapest.
                self.provider_blocked_until.pop(name, None)

        await asyncio.gather(*(one(k, p) for k, p in self.settings.providers.items()))
        self.persist()

    def persist(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(self.status(include_offers=True)))
            temp.replace(self.path)
        except OSError:
            log.warning("routing catalog cache could not be saved")

    async def run(self, http: httpx.AsyncClient) -> None:
        while True:
            await asyncio.sleep(min(120, self.refresh_seconds))
            if time.time() - self.refreshed >= self.refresh_seconds:
                await self.refresh(http)
            else:
                await self.refresh_balances(http)

    def status(self, include_offers: bool = False) -> dict:
        result = {
            "enabled": self.enabled,
            "updated": self.refreshed,
            "usd_rub": self.usd_rub,
            "usd_funding_fee_pct": self.usd_fee,
            "minimum_balance_usd": self.minimum_balance_usd,
            "minimum_balance_rub": self.minimum_balance_rub,
            "max_age_seconds": self.max_age,
            "errors": dict(self.errors),
            "balances": dict(self.balances),
            "provider_blocked_until": {name: until for name, until in self.provider_blocked_until.items() if until > time.time()},
            "vision_blocked": {key: until for key, until in self.vision_blocked.items() if until > time.time()},
            "vision_confirmed": {key: until for key, until in self.vision_confirmed.items() if until > time.time()},
            "vision_strikes": dict(self.vision_strikes),
            "providers": {name: len(rows) for name, rows in self.offers.items()},
        }
        if include_offers:
            result["offers"] = [asdict(o) for rows in self.offers.values() for o in rows]
        return result

    def _supplier_body(self, name: str, body: dict) -> dict:
        payload = dict(body)
        if "messages" in payload:
            # Defence in depth: every route to a provider gets the Chat Completions
            # shape, even when the caller bypassed `api.chat`.
            payload["messages"] = normalize_messages(payload["messages"])
        patch = _catalog_entry(name).get("body") or {}
        if isinstance(patch, dict):
            for key, value in patch.items():
                if key == "usage" and isinstance(value, dict) and isinstance(payload.get(key), dict):
                    payload[key] = {**payload[key], **value}
                else:
                    payload[key] = dict(value) if isinstance(value, dict) else value
        if name == "deepseek":
            thinking = payload.pop("enable_thinking", None)
            if "thinking" not in payload and isinstance(thinking, bool):
                payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
            if payload.get("reasoning_effort") == "none":
                payload.pop("reasoning_effort")
                payload.setdefault("thinking", {"type": "disabled"})
        if name == "aitunnel" and not self.allow_quantized:
            policy = payload.get("provider")
            if isinstance(policy, dict):
                # Preserve unknown precision (common for closed models); reject known 4-bit routes.
                policy.setdefault("quantizations", ["int8", "fp6", "fp8", "fp16", "bf16", "fp32", "unknown"])
        return payload

    def routes(
        self,
        spec: Any,
        body: dict,
        url: str,
        headers: dict,
        *,
        include_all: bool = False,
        family: bool = False,
    ) -> list[Route]:
        clean_body = {k: v for k, v in body.items() if k not in {"provider", "models", "route"}}
        configured = Route(spec.provider or "", url, headers, self._supplier_body(spec.provider or "", clean_body))
        if not self.enabled:
            return [configured]
        prompt = estimate_prompt(body)
        try:
            limit = int(body.get("max_completion_tokens") or body.get("max_tokens") or self.expected_output)
        except (TypeError, ValueError, OverflowError):
            limit = self.expected_output  # Preserve the original body for supplier validation.
        expected = limit
        tools = bool(body.get("tools") or body.get("functions"))
        vision = has_images(body.get("messages", []))
        candidates = []
        inventory = []
        now = time.time()
        anchor = model_key(getattr(spec, "id", "")) in FAMILY_ANCHORS or model_key(spec.upstream) in FAMILY_ANCHORS
        use_family = bool(family or anchor)
        wanted = identity(spec.upstream) if use_family else model_key(spec.upstream)
        if spec.provider and use_family:
            configured_offer = next((o for o in self.offers.get(spec.provider, []) if o.model == spec.upstream), None)
            if configured_offer:
                wanted = identity(configured_offer.canonical_model or configured_offer.model)
        for name, offers in self.offers.items():
            provider = self.settings.providers.get(name)
            if provider is None:
                continue
            if not provider.key() and not include_all:
                continue
            provider_reasons = []
            if not provider.key():
                provider_reasons.append("ключ поставщика не настроен")
            if self.provider_blocked_until.get(name, 0) > now:
                if not include_all:
                    continue
                provider_reasons.append("временная блокировка после отказа поставщика")
            balance = self.balances.get(name, {})
            if self.below_balance_floor(balance) and now - balance.get("checked", 0) < 300:
                if not include_all:
                    continue
                provider_reasons.append("баланс ниже рабочего порога")
            for offer in offers:
                # canonical_model is the customer-facing family alias; exact mode
                # must compare the provider's own model id, not that alias.
                offer_model = (offer.canonical_model or offer.model) if use_family else offer.model
                matched = identity(offer_model) == wanted if use_family else model_key(offer_model) == wanted
                if not matched:
                    continue
                exclusions = list(provider_reasons)
                if now - offer.updated > self.max_age:
                    exclusions.append("котировка устарела")
                if offer.valid_until and now >= offer.valid_until:
                    exclusions.append("истёк срок действия тарифа")
                if self.quarantined.get((name, offer.model.lower()), 0) > now:
                    exclusions.append("временно отключена после расхождения цены")
                if not self.allow_quantized and any(q in offer.endpoint.lower() for q in ("fp4", "int4", "q4")):
                    exclusions.append("квантованная версия исключена правилами")
                if "flex" in offer.endpoint.lower():
                    # flex — дешевле, но медленнее и с низким приоритетом; клиентам не отдаём
                    exclusions.append("flex-тариф: медленнее обычного")
                if tools and "tools" not in offer.parameters:
                    exclusions.append("не поддерживает вызов инструментов")
                pair = offer_key(name, offer.model)
                siblings = self.vision_siblings(name, offer, now) if vision else ()
                if vision and "image" not in offer.modalities and self.vision_confirmed.get(pair, 0) <= now:
                    # The catalog says text, but the same family at this supplier has a
                    # snapshot that sees: keep the supplier, swap the id.
                    if siblings:
                        offer = siblings[0]
                        siblings = siblings[1:]
                        pair = offer_key(name, offer.model)
                    else:
                        exclusions.append("не поддерживает изображения")
                if vision and self.vision_blocked.get(pair, 0) > now:
                    if siblings:
                        offer = siblings[0]
                        siblings = siblings[1:]
                        pair = offer_key(name, offer.model)
                    else:
                        exclusions.append("поставщик отказал в приёме изображений")
                if offer.context and prompt + limit > offer.context:
                    exclusions.append("недостаточный контекст")
                if offer.output_limit and limit > offer.output_limit:
                    exclusions.append("запрошенный ответ превышает лимит модели")
                if exclusions and not include_all:
                    continue
                payload = self._supplier_body(name, dict(clean_body, model=offer.model))
                if "max_tokens" in payload and "max_tokens" not in offer.parameters and "max_completion_tokens" in offer.parameters:
                    payload["max_completion_tokens"] = payload.pop("max_tokens")
                if (
                    "max_completion_tokens" in payload
                    and "max_completion_tokens" not in offer.parameters
                    and "max_tokens" in offer.parameters
                ):
                    payload["max_tokens"] = payload.pop("max_completion_tokens")
                inp, out, cache = offer.rates(now, prompt)
                fixed = replace(offer, input_rub=inp, output_rub=out, cached_rub=cache, overrides=())
                estimated_cost = fixed.estimate(prompt, expected)
                available_rub = number(balance.get("rub_estimate"))
                balance_is_fresh = balance.get("error") is None and available_rub is not None and now - balance.get("checked", 0) < 300
                if balance_is_fresh and available_rub < estimated_cost:
                    if not include_all:
                        continue
                    exclusions.append("баланса недостаточно для оценки запроса")
                route = Route(
                    name,
                    provider.base.rstrip("/") + "/v1/chat/completions",
                    {"Authorization": "Bearer " + provider.key(), "Content-Type": "application/json"},
                    payload,
                    fixed,
                    routing_eligible=not exclusions,
                    exclusion_reason="; ".join(dict.fromkeys(exclusions)),
                    siblings=tuple(other for other in siblings if other.model != offer.model),
                )
                ranked = (estimated_cost, name != spec.provider, route)
                inventory.append(ranked)
                if not exclusions:
                    candidates.append(ranked)
        # One request per supplier and model id: a substituted sibling is the same call
        # as the sibling's own offer, and sending it twice only doubles the wait.
        unique: dict[tuple[str, str], tuple[float, bool, Route]] = {}
        for cost, deprioritized, route in candidates:
            key = (route.provider, str(route.body.get("model") or ""))
            if key not in unique or cost < unique[key][0]:
                unique[key] = (cost, deprioritized, route)
        candidates = list(unique.values())
        candidates.sort(key=lambda item: (item[0], item[1], item[2].provider))
        inventory.sort(key=lambda item: (item[0], item[1], item[2].provider))
        if include_all:
            return [item[2] for item in inventory]
        # Keep every compatible, priced provider as a fallback. A relative-price
        # cutoff would defeat failover when the cheapest provider has no funds.
        routes = [item[2] for item in candidates]
        # Only the explicitly configured route is allowed as an unpriced last resort.
        # Never send the model to arbitrary known providers or a different model version.
        has_quote = any(
            (identity(o.canonical_model or o.model) == wanted if use_family else model_key(o.model) == wanted)
            for offers in self.offers.values()
            for o in offers
        )
        balance = self.balances.get(configured.provider, {})
        blocked = self.below_balance_floor(balance) and now - balance.get("checked", 0) < 300
        if (
            (not has_quote or self.allow_unpriced)
            and (configured.provider != "deepseek" or self.allow_unpriced)
            and not blocked
            and not any(
                r.routing_eligible and r.provider == configured.provider and r.body["model"] == configured.body["model"] for r in routes
            )
        ):
            routes.append(configured)
        return routes

    def accounting(self, route: Route, meta: dict, prompt: int, completion: int, cached: int) -> dict:
        result = dict(
            meta,
            provider=route.provider or "configured_upstream",
            routed_model=route.body["model"],
            routing_price_updated=route.offer.updated if route.offer else None,
            price_quote=(
                dict(
                    asdict(route.offer),
                    token_rate_unit="RUB per 1M tokens",
                    request_rate_unit="RUB per request",
                    provider_options=route.body.get("provider"),
                    usd_funding_fee_pct=self.usd_fee if route.provider == "openrouter" else 0,
                )
                if route.offer
                else None
            ),
        )
        actual = number(meta.get("provider_cost_rub"))
        if actual is not None:
            result.setdefault("provider_cost_basis", "reported_rub")
        elif route.offer:
            actual = route.offer.estimate(prompt, completion, cached)
            result["provider_cost_rub"] = actual
            result["provider_cost_basis"] = "catalog_estimate"
        if route.offer and actual is not None:
            expected = route.offer.estimate(prompt, completion, cached)
            difference = actual - expected
            ratio = actual / max(expected, 0.000001)
            result["provider_cost_expected_rub"] = round(expected, 8)
            result["provider_cost_difference_rub"] = round(difference, 8)
            result["provider_cost_ratio"] = round(ratio, 4)
            if difference >= self.price_mismatch_min_rub and ratio >= 1 + self.price_mismatch_pct / 100:
                key = (route.provider, route.body["model"].lower())
                until = time.time() + self.quarantine_seconds
                self.quarantined[key] = until
                # Ask the catalog loop to refresh soon. Until then, the bad offer is
                # excluded and routes() chooses the cheapest compatible alternative.
                self.refreshed = 0
                result["provider_price_mismatch"] = True
                result["provider_price_quarantined_until"] = int(until)
                log.error(
                    "provider price mismatch: provider=%s model=%s expected_rub=%.8f actual_rub=%.8f ratio=%.3f",
                    route.provider,
                    route.body["model"],
                    expected,
                    actual,
                    ratio,
                )
                last_alert = self.price_alerted.get(key, 0)
                if time.time() - last_alert >= self.quarantine_seconds:
                    self.price_alerted[key] = time.time()
                    result["provider_price_alert"] = True
        return result

    def note_failure(
        self,
        route: Route,
        status_code: int,
        body: bytes | str | None = None,
        proven: bool = False,
    ) -> None:
        """Quarantine a provider after payment-required; don't retry it on every turn.

        A picture refusal is only a fact when another route with the same body answered
        (`proven`): one oversized or broken image must not switch a supplier off. The
        pause grows with repeated strikes and is cleared by the first picture it takes.
        """
        text = body.decode("utf-8", "ignore") if isinstance(body, bytes) else str(body or "")
        if status_code == 400 and proven and route.provider and vision_refusal(text):
            pair = offer_key(route.provider, route.body.get("model") or "")
            strikes = self.vision_strikes.get(pair, 0) + 1
            self.vision_strikes[pair] = strikes
            pause = min(24 * 3600, 900 * (4 ** (strikes - 1)))
            until = time.time() + pause
            self.vision_blocked[pair] = until
            log.warning(
                "provider refused images: provider=%s model=%s strike=%s until=%s",
                route.provider,
                route.body.get("model") or "",
                strikes,
                int(until),
            )
            self.persist()
            return
        if status_code != 402 or not route.provider:
            return
        until = time.time() + 300
        self.provider_blocked_until[route.provider] = until
        self.balances[route.provider] = {
            **self.balances.get(route.provider, {}),
            "amount": 0,
            "checked": time.time(),
            "error": "HTTP 402; awaiting a successful balance check",
            "disabled_until": int(until),
        }
        log.error(
            "provider disabled after HTTP 402: provider=%s disabled_until=%s",
            route.provider,
            int(until),
        )
        self.persist()

    def note_success(self, route: Route, vision: bool = False) -> None:
        """A route answered. A picture it took proves the pair can see, whatever the
        catalog said, and lifts an earlier refusal at once."""
        if not route.provider:
            return
        pair = offer_key(route.provider, route.body.get("model") or "")
        changed = False
        if vision and self.vision_confirmed.get(pair, 0) <= time.time():
            self.vision_confirmed[pair] = time.time() + 7 * 24 * 3600
            changed = True
        if pair in self.vision_blocked or pair in self.vision_strikes:
            self.vision_blocked.pop(pair, None)
            self.vision_strikes.pop(pair, None)
            changed = True
        if changed:
            self.persist()

    def schedule_price_alert(self, http: httpx.AsyncClient, meta: dict) -> None:
        """Send an anomaly without blocking a user's model response; secrets stay in env."""
        if not meta.get("provider_price_alert"):
            return
        token = os.getenv("CLOUD_PRICE_ALERT_TELEGRAM_TOKEN", "").strip()
        chat_id = os.getenv("CLOUD_PRICE_ALERT_TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat_id:
            log.error("provider price alert cannot reach Telegram: alert credentials are not configured")
            return
        message = (
            "nanoLika: цена поставщика выше каталога.\n"
            f"Модель: {meta.get('routed_model', 'unknown')}\n"
            f"Поставщик: {meta.get('provider', 'unknown')}\n"
            f"Ожидалось: {meta.get('provider_cost_expected_rub', 0):.6f} ₽, "
            f"списано: {meta.get('provider_cost_rub', 0):.6f} ₽ "
            f"({meta.get('provider_cost_ratio', 0):.2f}×).\n"
            "Маршрут временно исключён. Если есть совместимая альтернатива, следующий запрос уйдёт через неё."
        )
        asyncio.create_task(self._send_price_alert(http, token, chat_id, message))

    @staticmethod
    async def _send_price_alert(http: httpx.AsyncClient, token: str, chat_id: str, message: str) -> None:
        try:
            response = await http.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message},
                timeout=8,
            )
            response.raise_for_status()
        except (httpx.HTTPError, ValueError):
            log.exception("provider price alert could not be delivered to Telegram")


def normalize_messages(messages: Any) -> Any:
    """Bring every content part to the Chat Completions shape the providers validate.

    Clients and older builds disagree on the names: the Responses API says
    `input_image`/`input_text`, Polza used to demand `input_image`, a bare string is
    legal in some SDKs. The relay speaks Chat Completions, so one shape goes out:
    text/refusal with a string, image_url/video_url with an object holding `url`,
    input_audio with `data` and `format`; unknown parts become text or are dropped.
    """
    if not isinstance(messages, list):
        return messages
    out = []
    for message in messages:
        if not isinstance(message, dict):
            out.append(message)
            continue
        content = message.get("content")
        if not isinstance(content, list):
            out.append(message)
            continue
        parts: list[dict[str, Any]] = []
        for part in content:
            if isinstance(part, str):
                parts.append({"type": "text", "text": part})
                continue
            if not isinstance(part, dict):
                parts.append({"type": "text", "text": str(part)})
                continue
            kind = str(part.get("type") or "")
            if kind in ("text", "refusal"):
                key = "text" if kind == "text" else "refusal"
                parts.append({"type": kind, key: str(part.get(key) or part.get("text") or "")})
            elif kind in ("image_url", "input_image"):
                raw = part.get("image_url") or part.get("url") or ""
                url = raw.get("url") if isinstance(raw, dict) else raw
                if url:
                    parts.append({"type": "image_url", "image_url": {"url": str(url)}})
            elif kind in ("video_url", "input_video"):
                raw = part.get("video_url") or part.get("input_video") or part.get("url") or ""
                url = raw.get("url") if isinstance(raw, dict) else raw
                if url:
                    parts.append({"type": "video_url", "video_url": {"url": str(url)}})
            elif kind == "input_audio":
                audio = part.get("input_audio") if isinstance(part.get("input_audio"), dict) else part
                data = audio.get("data")
                if data:
                    parts.append(
                        {
                            "type": "input_audio",
                            "input_audio": {"data": str(data), "format": str(audio.get("format") or "wav")},
                        }
                    )
            elif kind in ("file", "document", "search_result"):
                parts.append(part)
            elif part.get("text") is not None:
                parts.append({"type": "text", "text": str(part["text"])})
            elif part.get("url"):
                parts.append({"type": "image_url", "image_url": {"url": str(part["url"])}})
        out.append({**message, "content": parts if parts else ""})
    return out


def offer_key(provider: str, model: str) -> str:
    """Stable key for one supplier/model pair in the refusal map."""
    return f"{provider}|{str(model).lower()}"


# A picture can be refused for its own sake — too big, wrong format, broken base64. That
# says nothing about the model's eyes, so it must never switch a supplier off.
_IMAGE_FAULT = re.compile(
    r"too large|payload|exceed|max(?:imum)? (?:size|dimension|resolution)|file size|mime|"
    r"decode|invalid image|unsupported image|image size|размер|слишком бол|формат",
    re.IGNORECASE,
)


def vision_refusal(text: str) -> bool:
    """True when an upstream answer means "this route cannot take pictures".

    Suppliers word it differently: Polza answers in Russian about `input_image`,
    OpenRouter talks about endpoints that support image input. The customer should not
    see any of it — the relay just moves to the next route.
    """
    if not text:
        return False
    low = " ".join(text.lower().split())
    if _IMAGE_FAULT.search(low):
        return False
    if "input_image" in low or "input image" in low:
        return True
    if "image" in low and ("no endpoint" in low or "support" in low or "vision" in low):
        return True
    return "изображен" in low and ("поддерж" in low or "принима" in low or "видит" in low)


def has_images(messages: Any) -> bool:
    """True when the conversation carries a picture, whatever name the part has."""
    try:
        text = json.dumps(messages, ensure_ascii=False)
    except (TypeError, ValueError):
        return False
    return '"image_url"' in text or '"input_image"' in text


def retryable(response: httpx.Response, body: bytes | None = None) -> bool:
    if response.status_code in {401, 402, 403, 404, 408, 429} or response.status_code >= 500:
        return True
    if response.status_code == 400:
        try:
            data = response.json()
        except (ValueError, AttributeError):
            data = None
        if isinstance(data, dict):
            code = str((data.get("error") or {}).get("code", ""))
            if code in {"unsupported_parameter", "model_not_found", "unsupported_model"} or data.get("detail") == "Model not supported":
                return True
        text = body.decode("utf-8", "ignore") if isinstance(body, bytes) else str(body or "")
        return vision_refusal(text)
    return False


async def request_chat(
    http: httpx.AsyncClient,
    routes: list[Route],
    stream: bool,
    on_failure=None,
    on_success=None,
    vision: bool = False,
) -> tuple[httpx.Response, Route]:
    last: httpx.HTTPError | None = None
    failures: list[str] = []
    refused: list[tuple[Route, int, bytes]] = []
    for index, route in enumerate(routes):
        # The same supplier, other ids of the same family: a picture refused by one id is
        # tried on a sibling before the turn leaves the supplier at all.
        attempts: list[Route] = [route]
        for sibling in route.siblings if vision else ():
            attempts.append(
                replace(route, body={**route.body, "model": sibling.model}, offer=sibling)
            )
        for position, attempt in enumerate(attempts):
            try:
                request = http.build_request("POST", attempt.url, headers=attempt.headers, json=attempt.body)
                response = await http.send(request, stream=stream)
            except httpx.HTTPError as exc:
                last = exc
                provider = attempt.provider or "configured"
                model = str(attempt.body.get("model") or "")
                failure = f"{provider}/{model}:{type(exc).__name__}"
                failures.append(failure)
                # Never log response bodies, URLs, or credentials; identify only the route and failure class.
                log.warning("routing transport failure provider=%s model=%s error=%s", provider, model, type(exc).__name__)
                continue
            if response.status_code >= 400:
                # Read the refusal once: both the failure hook and `retryable` judge it.
                # A streaming response has no `content` until it is read, and touching it
                # on a good reply would end the stream with ResponseNotRead.
                await response.aread()
                refusal = response.content
                # Payment refusals count at once; a picture refusal is a fact only once
                # another route with the same body has answered (see the success branch).
                if on_failure is not None:
                    on_failure(attempt, response.status_code, refusal, False)
                more_attempts = position + 1 < len(attempts)
                if retryable(response, refusal) and (more_attempts or index + 1 < len(routes)):
                    log.warning("routing retry %s HTTP %s", attempt.provider or "configured", response.status_code)
                    refused.append((attempt, response.status_code, refusal))
                    await response.aclose()
                    continue
                return response, attempt
            # Answered: the refusals before it are now proven to be about the route, not
            # about the request, so each one may be remembered.
            if on_failure is not None:
                for failed_route, status, refusal in refused:
                    on_failure(failed_route, status, refusal, True)
            if on_success is not None:
                on_success(attempt, vision)
            return response, attempt
    detail = ", ".join(failures) if failures else "no routes"
    raise httpx.ConnectError(f"No configured model provider answered ({detail})") from last
