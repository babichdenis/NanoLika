"""A reference catalogue of official model prices and capabilities.

The relay's own menu knows what *we* buy and what *we* sell. This module adds a
second, clearly separate layer: vendor list prices and model facts from the
open Ferro Labs catalogue (Apache-2.0). It is used for the admin's "official"
column and to fill a missing context/output ceiling — never for billing,
routing or the public retail price.

Source: https://github.com/ferro-labs/model-catalog
Format: one JSON object keyed by ``provider/model_id`` (schema v2).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

MANIFEST_URL = "https://github.com/ferro-labs/model-catalog/releases/latest/download/manifest.json"
CATALOG_URL = "https://github.com/ferro-labs/model-catalog/releases/latest/download/catalog.json"
UA = "nanoLika-cloud/1.0 (model reference catalogue)"
CACHE = Path(os.environ.get("MODEL_REFERENCE_PATH") or Path(__file__).resolve().parent / "console" / "providers" / "ferro.json")
MAX_AGE_S = 7 * 86400

_index: dict[str, dict[str, Any]] | None = None
_info: dict[str, Any] | None = None
_norm_index: tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]] | None = None
_refresh_lock = threading.Lock()
_refreshing = False


def _get(url: str, timeout: int = 120) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    return gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body


def _load_file() -> tuple[dict[str, dict[str, Any]], dict[str, Any]] | None:
    try:
        payload = json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
    models = payload.get("models")
    if not isinstance(models, dict) or not models:
        return None
    return models, info


def _compact(source: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Keep only the fields an operator can use; leave provider wrappers out."""
    out: dict[str, dict[str, Any]] = {}
    for key, row in source.items():
        if not isinstance(row, dict) or not key or "/" not in key:
            continue
        provider, model_id = key.split("/", 1)
        pricing = row.get("pricing") if isinstance(row.get("pricing"), dict) else {}
        caps = row.get("capabilities") if isinstance(row.get("capabilities"), dict) else {}
        life = row.get("lifecycle") if isinstance(row.get("lifecycle"), dict) else {}
        out[key] = {
            "key": key,
            "provider": str(row.get("provider") or provider),
            "model_id": str(row.get("model_id") or model_id),
            "display_name": str(row.get("display_name") or model_id),
            "mode": str(row.get("mode") or "chat"),
            "context": int(row.get("context_window") or 0),
            "max_output": int(row.get("max_output_tokens") or 0),
            "pricing": {
                field: pricing.get(field)
                for field in (
                    "input_per_m_tokens",
                    "output_per_m_tokens",
                    "cache_read_per_m_tokens",
                    "cache_write_per_m_tokens",
                    "reasoning_per_m_tokens",
                    "image_per_tile",
                )
            },
            "capabilities": {
                field: bool(caps.get(field))
                for field in (
                    "vision",
                    "audio_input",
                    "audio_output",
                    "function_calling",
                    "parallel_tool_calls",
                    "json_mode",
                    "response_schema",
                    "prompt_caching",
                    "reasoning",
                    "streaming",
                )
            },
            "status": str(life.get("status") or "ga"),
            "deprecation_date": life.get("deprecation_date"),
            "sunset_date": life.get("sunset_date"),
            "successor": life.get("successor"),
            "source": row.get("source") or "",
            "updated_at": row.get("updated_at") or "",
        }
    return out


def refresh(force: bool = False) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Read the cached catalogue, or fetch and verify a new one.

    The cache is a normal JSON file next to the provider price snapshot. It is
    refreshed at most once a week; a failed fetch never removes the last good
    copy.
    """
    global _index, _info
    cached = _load_file()
    if cached and not force:
        models, info = cached
        fetched = float(info.get("fetched_at") or 0)
        if fetched and time.time() - fetched < MAX_AGE_S:
            _index, _info = models, info
            return models, info

    try:
        manifest = json.loads(_get(MANIFEST_URL, timeout=45).decode("utf-8"))
        catalog = _get(CATALOG_URL, timeout=150)
        digest = hashlib.sha256(catalog).hexdigest()
        expected = str((manifest or {}).get("catalog_sha256") or "")
        if expected and digest != expected:
            raise ValueError("ferro catalogue SHA-256 does not match its manifest")
        source = json.loads(catalog.decode("utf-8"))
        if not isinstance(source, dict):
            raise ValueError("ferro catalogue is not an object")
        models = _compact(source)
        if not models:
            raise ValueError("ferro catalogue has no usable models")
        info = {
            "version": str(manifest.get("version") or ""),
            "generated_at": str(manifest.get("generated_at") or ""),
            "fetched_at": int(time.time()),
            "sha256": digest,
            "source": "https://github.com/ferro-labs/model-catalog",
            "license": "Apache-2.0",
            "count": len(models),
        }
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({"info": info, "models": models}, ensure_ascii=False), encoding="utf-8")
        _index, _info = models, info
        return models, info
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        if cached:
            _index, _info = cached
            return cached
        _index, _info = {}, {"error": "reference catalogue is not available"}
        return _index, _info


def index() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """The cached index, without network access."""
    global _index, _info
    if _index is not None:
        return _index, _info or {}
    cached = _load_file()
    if cached:
        _index, _info = cached
    else:
        _index, _info = {}, {"error": "reference catalogue is not loaded"}
    return _index, _info or {}


def age_seconds() -> float:
    """How old the cached catalogue is; infinite when there is no cache yet."""
    _, info = index()
    try:
        fetched = float(info.get("fetched_at") or 0)
    except (TypeError, ValueError):
        fetched = 0.0
    return float("inf") if fetched <= 0 else max(0.0, time.time() - fetched)


def stale() -> bool:
    return age_seconds() > MAX_AGE_S


def refresh_async() -> None:
    """Fetch a new catalogue in the background; the caller never waits for GitHub.

    The admin page must stay fast even when GitHub is slow: it renders the last
    good copy this request and the next one picks up the fresh version.
    """
    global _refreshing
    if not stale():
        return
    with _refresh_lock:
        if _refreshing:
            return
        _refreshing = True

    def work() -> None:
        global _refreshing
        try:
            refresh(force=True)
        finally:
            with _refresh_lock:
                _refreshing = False

    threading.Thread(target=work, name="ferro-catalog", daemon=True).start()


def _norm(value: object) -> str:
    text = str(value or "").lower().replace("_", "-").replace(".", "-")
    return "".join(ch for ch in text if ch.isalnum() or ch == "-").strip("-")


def _by_model(models: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Normalised model-id index, built once per loaded catalogue.

    `for_spec` runs for every row of the admin table (~500 models); rebuilding
    a 3 000-row index each time made the whole page take twenty seconds.
    """
    global _norm_index
    if _norm_index is not None and _norm_index[0] is models:
        return _norm_index[1]
    out: dict[str, list[dict[str, Any]]] = {}
    for row in models.values():
        out.setdefault(_norm(row.get("model_id")), []).append(row)
    _norm_index = (models, out)
    return out


def for_spec(spec: Any, models: dict[str, dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """The closest reference row for one of our ModelSpecs, or None.

    Matching is deliberately conservative: exact ``upstream``, exact model id,
    then a normalised model id. A name that only looks similar is not enough.
    """
    if models is None:
        models, _ = index()
    if not models:
        return None
    upstream = str(getattr(spec, "upstream", "") or "")
    our_id = str(getattr(spec, "id", "") or "")
    priced_as = str(getattr(spec, "priced_as", "") or "")
    normalized = _by_model(models)
    for candidate in (upstream, our_id, priced_as):
        if not candidate:
            continue
        exact = models.get(candidate.lower())
        if exact:
            return dict(exact, matched_by="key")
        bare = candidate.rsplit("/", 1)[-1].lower()
        exact_id = models.get(bare)
        if exact_id:
            return dict(exact_id, matched_by="model_id")
        rows = normalized.get(_norm(bare)) or []
        if rows:
            # Prefer the row whose provider is named in the upstream prefix.
            prefix = candidate.split("/", 1)[0].lower() if "/" in candidate else ""
            rows = sorted(rows, key=lambda row: (str(row.get("provider") or "").lower() != prefix, str(row.get("key") or "")))
            return dict(rows[0], matched_by="normalized")
    return None


def describe(spec: Any, models: dict[str, dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """The small row the admin table needs, without capabilities and sources."""
    row = for_spec(spec, models)
    if not row:
        return None
    pricing = row.get("pricing") if isinstance(row.get("pricing"), dict) else {}
    return {
        "key": row.get("key") or "",
        "input_usd": pricing.get("input_per_m_tokens"),
        "output_usd": pricing.get("output_per_m_tokens"),
        "cache_read_usd": pricing.get("cache_read_per_m_tokens"),
        "context": int(row.get("context") or 0),
        "max_output": int(row.get("max_output") or 0),
        "status": row.get("status") or "",
        "deprecation_date": row.get("deprecation_date"),
        "sunset_date": row.get("sunset_date"),
        "successor": row.get("successor"),
        "matched_by": row.get("matched_by") or "",
    }


def context_for(spec: Any) -> int:
    """A reference context ceiling, used only when our own spec has none."""
    row = for_spec(spec)
    try:
        return int((row or {}).get("context") or 0)
    except (TypeError, ValueError):
        return 0


def output_for(spec: Any) -> int:
    row = for_spec(spec)
    try:
        return int((row or {}).get("max_output") or 0)
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":  # python -m nanomuse_cloud.reference_catalog
    models, info = refresh(force=True)
    print(json.dumps({"count": len(models), **info}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if models else 1)
