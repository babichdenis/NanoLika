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

log = logging.getLogger(__name__)
FLASH_1017 = "deepseek/deepseek-v4.1-flash-1017"
FLASH_FAMILY = "deepseek/deepseek-v4.1-flash"
ALIASES = {
    # Keep a clean family identity for comparisons, but never route the dated
    # 1017 endpoint as the default Flash model. Its current quote is a costly
    # endpoint variant, not the price of the generic DeepSeek Flash offer.
    "deepseek-flash": FLASH_FAMILY,
    "deepseek-v4.1-flash": FLASH_FAMILY,
    "deepseek/deepseek-v4.1-flash": FLASH_FAMILY,
    "deepseek-v4.1-flash-1017": FLASH_FAMILY,
    FLASH_1017: FLASH_FAMILY,
}
VENDORS = {"deepseek", "openai", "anthropic", "google", "qwen", "z-ai", "meta-llama"}
# Names with an implemented authenticated model-catalog and priced chat-route adapter.
# This is deliberately narrower than the market-reference catalog in model_catalog.py.
ROUTING_CATALOG_PROVIDERS = frozenset({"polza", "routerai", "openrouter", "aitunnel", "proxyapi"})


def identity(model: str) -> str:
    return ALIASES.get(model.lower(), model)


def blocked_price_variant(model: str) -> bool:
    """Known expensive dated Flash endpoint; do not use as generic Flash."""
    return model.lower().removeprefix("deepseek/") == "deepseek-v4.1-flash-1017"


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

    def rates(self, at: float | None = None) -> tuple[float, float, float | None]:
        now = datetime.fromtimestamp(time.time() if at is None else at, UTC)
        minute = now.hour * 100 + now.minute
        day = now.strftime("%A").lower()
        for rule in self.overrides:
            if rule.get("utc_days") and day not in rule["utc_days"]:
                continue
            start, end = int(rule.get("utc_start", 0)), int(rule.get("utc_end", 0))
            matches = start == end or (start <= minute < end if start < end else minute >= start or minute < end)
            if matches:
                return rule["input_rub"], rule["output_rub"], rule.get("cached_rub")
        return self.input_rub, self.output_rub, self.cached_rub

    def estimate(self, prompt: int, output: int, cached: int = 0) -> float:
        inp, out, cache = self.rates()
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
        self.max_fallback_multiple = max(1.0, float(os.getenv("CLOUD_ROUTING_MAX_FALLBACK_MULTIPLE", "3")))
        self.allow_quantized = os.getenv("CLOUD_ROUTING_ALLOW_QUANTIZED", "0") == "1"
        self.allow_unpriced = os.getenv("CLOUD_ROUTING_ALLOW_UNPRICED_FALLBACK", "0") == "1"
        self.enabled = os.getenv("CLOUD_AUTO_ROUTING", "1") == "1"
        self.price_mismatch_pct = max(0.0, float(os.getenv("CLOUD_ROUTING_PRICE_MISMATCH_PCT", "25")))
        self.price_mismatch_min_rub = max(0.0, float(os.getenv("CLOUD_ROUTING_PRICE_MISMATCH_MIN_RUB", "0.02")))
        self.quarantine_seconds = max(self.refresh_seconds, int(os.getenv("CLOUD_ROUTING_PRICE_QUARANTINE_SECONDS", "1800")))
        self.quarantined: dict[tuple[str, str], float] = {}
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
            except (OSError, ValueError, TypeError, KeyError):
                pass

    def parse(self, provider: str, data: Any, at: float) -> list[Offer]:
        """Only supported schemas with explicit currency and separate token units."""
        if provider == "aitunnel":
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
            if provider == "polza":
                if currency != "RUB" or row.get("type") != "chat":
                    continue
                inp = number(price.get("prompt_per_million"))
                out = number(price.get("completion_per_million"))
                cache = number(price.get("input_cache_read_per_million"))
                parameters = endpoint.get("supported_parameters") or []
            elif provider == "proxyapi":
                if currency != "RUB":
                    continue
                inp, out = number(price.get("input_tokens")), number(price.get("output_tokens"))
                cache = number(price.get("cache_read_input_tokens"))
                parameters = row.get("supported_parameters") or []
            elif provider in {"openrouter", "routerai"}:
                factor = 1_000_000.0
                if provider == "openrouter":
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
            elif provider == "aitunnel":
                inp, out = number(row.get("prompt_cost")), number(row.get("completion_cost"))
                discount = number(row.get("cache_discount"))
                cache = inp * (1 - discount) if inp is not None and discount is not None and discount <= 1 else None
                parameters = row.get("supported_parameters") or []
            else:
                continue  # An unpriced model list is not a supplier offer.
            if inp is None or out is None or inp == out == 0:
                continue
            architecture = row.get("architecture") or row.get("modalities") or {}
            modalities = architecture.get("input_modalities") or architecture.get("input") or ["text"]
            outputs = architecture.get("output_modalities") or architecture.get("output") or ["text"]
            if "text" not in outputs:
                continue
            vendor = str(row.get("provider") or "")
            canonical = f"{vendor}/{model}" if provider == "aitunnel" and "/" not in model and vendor in VENDORS else model
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
                url = provider.base.rstrip("/") + "/v1/models"
                headers = {"Authorization": "Bearer " + provider.key()}
                if name == "aitunnel":
                    url = "https://api.aitunnel.ru/public/aitunnel/models/chat"
                    headers = {}
                if name == "proxyapi":
                    url = "https://www.proxyapi.ru/models/browse"
                    headers = {}
                try:
                    response = await http.get(url, headers=headers, timeout=25)
                    response.raise_for_status()
                    data = proxy_catalog(response.text) if name == "proxyapi" else response.json()
                    if name == "proxyapi":
                        # A public price is eligible only when this key can see the model.
                        catalog = await http.get(
                            provider.base.rstrip("/") + "/v1/models", headers={"Authorization": "Bearer " + provider.key()}, timeout=25
                        )
                        catalog.raise_for_status()
                        allowed = {row["id"] for row in catalog.json().get("data", [])}
                        data["data"] = [row for row in data["data"] if row["id"] in allowed]
                    offers = self.parse(name, data, time.time())
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
            paths = {
                "deepseek": "/user/balance",
                "polza": "/v1/balance",
                "routerai": "/v1/credits",
                "openrouter": "/v1/credits",
                "aitunnel": "/v1/aitunnel/balance",
                "proxyapi": "/proxyapi/balance",
                "proxyapi-openai": "/proxyapi/balance",
            }
            if name not in paths or not provider.key():
                return
            # DeepSeek и OpenRouter считают в долларах, остальные продавцы — в рублях
            currency = "USD" if name in ("openrouter", "deepseek") else "RUB"
            entry = {"amount": None, "currency": currency, "checked": time.time(), "error": None}
            base = provider.base.rstrip("/")
            if name == "proxyapi-openai":
                base = base.removesuffix("/openai")
            key = os.getenv("OPENROUTER_BALANCE_KEY", provider.key()) if name == "openrouter" else provider.key()
            try:
                response = await http.get(base + paths[name], headers={"Authorization": "Bearer " + key}, timeout=15)
                response.raise_for_status()
                data = response.json()
                if name == "deepseek":
                    # {"is_available": false, "balance_infos": [{"currency": "USD", "total_balance": "-0.00"}, …]}
                    infos = data.get("balance_infos") or []
                    for info in infos:
                        if str(info.get("currency")).upper() == "USD":
                            amount = number(info.get("total_balance"))
                            break
                    entry["available"] = bool(data.get("is_available"))
                    entry["cny"] = next(
                        (
                            number(i.get("total_balance"))
                            for i in infos
                            if str(i.get("currency")).upper() == "CNY"
                        ),
                        None,
                    )
                elif name == "openrouter":
                    data = data.get("data", {})
                    total, used = number(data.get("total_credits")), number(data.get("total_usage"))
                    amount = total - used if total is not None and used is not None else None
                elif name == "routerai":
                    amount = number(data.get("data", {}).get("credits"))
                elif name == "polza":
                    amount = number(data.get("available"))
                else:
                    amount = number(data.get("balance"))
                    budget = data.get("budget")
                    if name == "aitunnel" and number(budget) is not None:
                        entry["key_budget"] = number(budget)
                        amount = min(amount, number(budget)) if amount is not None else None
                    if name.startswith("proxyapi") and isinstance(budget, dict):
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

        await asyncio.gather(*(one(k, p) for k, p in self.settings.providers.items()))

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
            "max_age_seconds": self.max_age,
            "errors": dict(self.errors),
            "max_fallback_multiple": self.max_fallback_multiple,
            "balances": dict(self.balances),
            "providers": {name: len(rows) for name, rows in self.offers.items()},
        }
        if include_offers:
            result["offers"] = [asdict(o) for rows in self.offers.values() for o in rows]
        return result

    def routes(self, spec: Any, body: dict, url: str, headers: dict, *, include_all: bool = False) -> list[Route]:
        clean_body = {k: v for k, v in body.items() if k not in {"provider", "models", "route"}}
        configured = Route(spec.provider or "", url, headers, clean_body)
        if not self.enabled:
            return [configured]
        prompt = estimate_prompt(body)
        try:
            limit = int(body.get("max_completion_tokens") or body.get("max_tokens") or self.expected_output)
        except (TypeError, ValueError, OverflowError):
            limit = self.expected_output  # Preserve the original body for supplier validation.
        expected = min(limit, self.expected_output)
        tools = bool(body.get("tools") or body.get("functions"))
        messages = json.dumps(body.get("messages", []))
        vision = '"image_url"' in messages or '"input_image"' in messages
        candidates = []
        blocked_candidates = []
        now = time.time()
        wanted = identity(spec.upstream)
        if spec.provider:
            configured_offer = next((o for o in self.offers.get(spec.provider, []) if o.model == spec.upstream), None)
            if configured_offer:
                wanted = identity(configured_offer.canonical_model or configured_offer.model)
        for name, offers in self.offers.items():
            provider = self.settings.providers.get(name)
            if provider is None or not provider.key():
                continue
            if self.provider_blocked_until.get(name, 0) > now:
                continue
            balance = self.balances.get(name, {})
            if (
                balance.get("error") is None
                and balance.get("amount") is not None
                and balance["amount"] <= 0
                and now - balance.get("checked", 0) < 300
            ):
                continue
            for offer in offers:
                if identity(offer.canonical_model or offer.model) != wanted or now - offer.updated > self.max_age:
                    continue
                if blocked_price_variant(offer.model):
                    if include_all:
                        fixed = replace(offer, overrides=())
                        blocked_candidates.append(Route(
                            name,
                            provider.base.rstrip("/") + "/v1/chat/completions",
                            {"Authorization": "Bearer " + provider.key(), "Content-Type": "application/json"},
                            dict(clean_body, model=offer.model),
                            fixed,
                            False,
                            "исключена дорогая версия 1017; не используется как обычный Flash",
                        ))
                    continue
                if self.quarantined.get((name, offer.model.lower()), 0) > now:
                    continue
                if not self.allow_quantized and any(q in offer.endpoint.lower() for q in ("fp4", "int4", "q4")):
                    continue
                if tools and "tools" not in offer.parameters:
                    continue
                if vision and "image" not in offer.modalities:
                    continue
                if offer.context and prompt + limit > offer.context:
                    continue
                if offer.output_limit and limit > offer.output_limit:
                    continue
                payload = dict(clean_body, model=offer.model)
                if "max_tokens" in payload and "max_tokens" not in offer.parameters and "max_completion_tokens" in offer.parameters:
                    payload["max_completion_tokens"] = payload.pop("max_tokens")
                if (
                    "max_completion_tokens" in payload
                    and "max_completion_tokens" not in offer.parameters
                    and "max_tokens" in offer.parameters
                ):
                    payload["max_tokens"] = payload.pop("max_completion_tokens")
                if name == "openrouter":
                    payload["provider"] = {"sort": "price", "require_parameters": True}
                    usage_options = payload.get("usage")
                    payload["usage"] = {
                        **(usage_options if isinstance(usage_options, dict) else {}),
                        "include": True,
                    }
                inp, out, cache = offer.rates(now)
                fixed = replace(offer, input_rub=inp, output_rub=out, cached_rub=cache, overrides=())
                route = Route(
                    name,
                    provider.base.rstrip("/") + "/v1/chat/completions",
                    {"Authorization": "Bearer " + provider.key(), "Content-Type": "application/json"},
                    payload,
                    fixed,
                )
                candidates.append((fixed.estimate(prompt, expected), name != spec.provider, route))
        candidates.sort(key=lambda item: (item[0], item[1], item[2].provider))
        eligible = []
        annotated = []
        if candidates:
            cap = candidates[0][0] * self.max_fallback_multiple
            for estimate, configured_first, route in candidates:
                allowed = estimate <= cap
                checked = replace(
                    route,
                    routing_eligible=allowed,
                    exclusion_reason="" if allowed else "стоимость этого запроса выше допустимого резерва",
                )
                annotated.append((estimate, configured_first, checked))
                if allowed:
                    eligible.append((estimate, configured_first, checked))
        routes = [item[2] for item in (annotated if include_all else eligible)]
        if include_all:
            routes.extend(blocked_candidates)
        # Only the explicitly configured route is allowed as an unpriced last resort.
        # Never send the model to arbitrary known providers or a different model version.
        has_quote = any(
            identity(o.canonical_model or o.model) == wanted and not blocked_price_variant(o.model)
            for offers in self.offers.values() for o in offers
        )
        balance = self.balances.get(configured.provider, {})
        blocked = (
            balance.get("error") is None
            and balance.get("amount") is not None
            and balance["amount"] <= 0
            and now - balance.get("checked", 0) < 300
        )
        if (
            (not has_quote or self.allow_unpriced)
            and not blocked
            and not any(r.routing_eligible and r.provider == configured.provider and r.body["model"] == configured.body["model"] for r in routes)
        ):
            routes.append(configured)
        return routes

    def accounting(self, route: Route, meta: dict, prompt: int, completion: int, cached: int) -> dict:
        result = dict(
            meta,
            provider=route.provider or "configured_upstream",
            routed_model=route.body["model"],
            routing_price_updated=route.offer.updated if route.offer else None,
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
                    route.provider, route.body["model"], expected, actual, ratio,
                )
                last_alert = self.price_alerted.get(key, 0)
                if time.time() - last_alert >= self.quarantine_seconds:
                    self.price_alerted[key] = time.time()
                    result["provider_price_alert"] = True
        return result

    def note_failure(self, route: Route, status_code: int) -> None:
        """Quarantine a provider after payment-required; don't retry it on every turn."""
        if status_code != 402 or not route.provider:
            return
        until = time.time() + max(self.quarantine_seconds, 1800)
        self.provider_blocked_until[route.provider] = until
        self.balances[route.provider] = {
            **self.balances.get(route.provider, {}),
            "amount": 0,
            "checked": time.time(),
            "error": "HTTP 402; temporarily disabled",
            "disabled_until": int(until),
        }
        log.error(
            "provider disabled after HTTP 402: provider=%s disabled_until=%s",
            route.provider, int(until),
        )

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


def retryable(response: httpx.Response) -> bool:
    if response.status_code in {401, 402, 403, 404, 408, 429} or response.status_code >= 500:
        return True
    if response.status_code == 400:
        try:
            data = response.json()
            code = str((data.get("error") or {}).get("code", ""))
            return code in {"unsupported_parameter", "model_not_found", "unsupported_model"} or data.get("detail") == "Model not supported"
        except (ValueError, AttributeError):
            return False
    return False


async def request_chat(http: httpx.AsyncClient, routes: list[Route], stream: bool, on_failure=None) -> tuple[httpx.Response, Route]:
    last: httpx.HTTPError | None = None
    for index, route in enumerate(routes):
        try:
            request = http.build_request("POST", route.url, headers=route.headers, json=route.body)
            response = await http.send(request, stream=stream)
        except httpx.HTTPError as exc:
            last = exc
            continue
        if response.status_code >= 400:
            await response.aread()
            if on_failure is not None:
                on_failure(route, response.status_code)
        if retryable(response) and index + 1 < len(routes):
            log.warning("routing retry %s HTTP %s", route.provider or "configured", response.status_code)
            await response.aclose()
            continue
        return response, route
    raise httpx.ConnectError("No configured model provider answered") from last
