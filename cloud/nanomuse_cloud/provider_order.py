"""Where to ask for a model, cheapest first.

The menu already picks the cheapest company for each model when it is built, but a
menu is a snapshot: prices move, a provider has an outage, a key runs out of credit.
This module answers one question for a single request, `chain(settings, spec)`: the
places where this very model can be bought right now, in the order of their price,
with the model's own provider first when prices are equal. The caller tries them in
turn and bills whoever answered, so a cheaper company is used the moment it appears
in `prices.json`, and one provider's bad day no longer ends a person's turn.

Prices come from the operator's own table (`console/providers/prices.json`, the file
the providers page shows, refreshed by its timer). A model that table does not carry
keeps exactly one place: the one the menu named, as before.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

PRICES_PATH = Path(__file__).resolve().parent / "console" / "providers" / "prices.json"
CACHE_S = 60.0

_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "mtime": None, "offers": {}}


def _prices_file() -> Path:
    import os

    return Path(os.environ.get("PROVIDER_PRICES_JSON") or PRICES_PATH)


def offers_by_model() -> dict[str, list[tuple[str, float]]]:
    """{model name: [(provider, rubles)]} out of the operator's price table, newest first.

    Read once a minute: the timer rewrites the file, and a request is not the place to
    parse a megabyte of JSON.
    """
    path = _prices_file()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    now = time.monotonic()
    with _lock:
        if _cache["mtime"] == mtime and now - _cache["at"] < CACHE_S:
            return _cache["offers"]  # type: ignore[return-value]
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    found: dict[str, list[tuple[str, float]]] = {}
    for model in data.get("models") or []:
        name = str(model.get("name") or "")
        if not name:
            continue
        seen: dict[str, float] = {}
        for offer in model.get("offers") or []:
            provider = str(offer.get("provider") or "")
            rub = offer.get("rub")
            if not provider or rub in (None, "", 0):
                continue
            price = float(rub)
            # у одного продавца бывает несколько цен (вход и выход): берём меньшую
            if provider not in seen or price < seen[provider]:
                seen[provider] = price
        if seen:
            found[name] = sorted(seen.items(), key=lambda kv: kv[1])
    with _lock:
        _cache.update({"at": now, "mtime": mtime, "offers": found})
    return found


def provider_order(model_id: str, known: list[str]) -> list[str]:
    """Providers for this model, cheapest first, limited to those the relay can reach.

    `known` are the connection names this relay has (`settings.providers`, plus "" for
    its own upstream). A model the price table does not know comes back as `known`.
    """
    table = offers_by_model().get(model_id) or []
    ordered = [name for name, _ in table if name in known]
    if not ordered:
        return list(known)
    rest = [name for name in known if name not in ordered]
    return ordered + rest
