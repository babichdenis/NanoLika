"""Direct DeepSeek quotes: official token rates and an explicitly verified calendar."""

from __future__ import annotations

import html
import json
import math
import os
import re
from datetime import UTC, datetime
from pathlib import Path

PRICE_URL = "https://api-docs.deepseek.com/quick_start/pricing/"
CALENDAR_SOURCE = "https://www.gov.cn/zhengce/content/202511/content_7047090.htm"
# State Council 2026 holiday notice. Weekend make-up work does not change the
# supplier's explicit Monday-Friday peak rule.
HOLIDAY_RANGES_2026 = [
    (1, 1, 3),
    (2, 15, 23),
    (4, 4, 6),
    (5, 1, 5),
    (6, 19, 21),
    (9, 25, 27),
    (10, 1, 7),
]


def holiday_dates(year: int) -> list[str]:
    path = os.getenv("CLOUD_DEEPSEEK_HOLIDAYS_FILE", "")
    if path:
        try:
            raw = json.loads(Path(path).read_text())
        except (OSError, ValueError) as error:
            raise ValueError("DeepSeek holiday calendar is unavailable") from error
        values = raw.get(str(year))
        if not isinstance(values, list) or any(not isinstance(x, str) for x in values):
            raise ValueError("DeepSeek holiday calendar year is unverified")
        for value in values:
            if datetime.strptime(value, "%Y-%m-%d").year != year:
                raise ValueError("DeepSeek holiday calendar date is invalid")
        return sorted(set(values))
    if year != 2026:
        raise ValueError("DeepSeek holiday calendar year is unverified; update the calendar")
    return [f"2026-{month:02d}-{day:02d}" for month, first, last in HOLIDAY_RANGES_2026 for day in range(first, last + 1)]


def catalog(page: str, models: dict, at: float, usd_rub: float) -> dict:
    """Join authenticated IDs/capabilities with the official USD/M pricing table.

    Reject changed/partial schemas; never promote a guessed tariff to a quote.
    """
    if not math.isfinite(usd_rub) or usd_rub <= 0:
        raise ValueError("DeepSeek FX rate is invalid")
    rows = []
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", page, re.DOTALL | re.IGNORECASE):
        cells = [
            " ".join(html.unescape(re.sub(r"<[^>]+>", " ", cell)).split())
            for cell in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row, re.DOTALL | re.IGNORECASE)
        ]
        rows.append(cells)
    headers = next((r for r in rows if r and r[0] == "MODEL"), [])
    ids = [re.sub(r"\s*\(\d+\)\s*$", "", value).strip() for value in headers[1:]]
    if ids != ["deepseek-flash", "deepseek-v4-pro"]:
        raise ValueError("DeepSeek pricing model columns changed")
    section = ""
    prices: dict[tuple[str, str], list[float]] = {}
    for cells in rows:
        text = " ".join(cells).upper()
        if "CACHE HIT" in text:
            section = "cache"
        elif "CACHE MISS" in text:
            section = "input"
        elif "1M OUTPUT TOKENS" in text:
            section = "output"
        period = "off" if "OFF-PEAK" in cells else "peak" if "PEAK" in cells else ""
        if not section or not period:
            continue
        money = [re.fullmatch(r"\$([0-9]+(?:\.[0-9]+)?)", cell) for cell in cells]
        values = [float(m.group(1)) for m in money if m]
        key = (section, period)
        if len(values) != len(ids) or key in prices or any(not math.isfinite(v) or v <= 0 for v in values):
            raise ValueError("DeepSeek token pricing table is incomplete")
        prices[key] = values
    if len(prices) != 6:
        raise ValueError("DeepSeek peak/off-peak prices are incomplete")
    checked = datetime.fromtimestamp(at, UTC)
    dates = holiday_dates(checked.year)
    valid_until = int(datetime(checked.year + 1, 1, 1, tzinfo=UTC).timestamp())
    result = []
    for model in models.get("data", []):
        model_id = model.get("id")
        if model_id not in ids:
            continue
        index = ids.index(model_id)
        rates = {
            period: {kind: prices[kind, period][index] * usd_rub for kind in ["input", "output", "cache"]} for period in ["off", "peak"]
        }
        off, peak = rates["off"], rates["peak"]
        if any(peak[k] < off[k] for k in off):
            raise ValueError("DeepSeek peak price is below off-peak")
        context, output = model.get("context_window"), model.get("max_output_tokens")
        inputs = model.get("input_modalities")
        if type(context) is not int or type(output) is not int or context <= 0 or output <= 0 or not isinstance(inputs, list):
            raise ValueError("DeepSeek model capabilities are missing")
        base = {
            "input_rub": off["input"],
            "output_rub": off["output"],
            "cached_rub": off["cache"],
        }
        overrides = [dict(base, utc_dates=dates, utc_start=0, utc_end=0)]
        for start, end in [(100, 400), (600, 1000)]:
            overrides.append(
                {
                    "input_rub": peak["input"],
                    "output_rub": peak["output"],
                    "cached_rub": peak["cache"],
                    "utc_days": ["monday", "tuesday", "wednesday", "thursday", "friday"],
                    "utc_start": start,
                    "utc_end": end,
                }
            )
        result.append(
            {
                "id": model_id,
                "context_length": context,
                "max_output": output,
                "supported_parameters": [
                    "tools",
                    "tool_choice",
                    "max_tokens",
                    "response_format",
                    "reasoning_effort",
                ],
                "architecture": {
                    "input_modalities": inputs,
                    "output_modalities": model.get("output_modalities", ["text"]),
                },
                "pricing": dict(base, overrides=overrides),
                "source_url": PRICE_URL,
                "source_currency": "USD",
                "fx_rub_per_unit": usd_rub,
                "valid_until": valid_until,
            }
        )
    if not result:
        raise ValueError("No documented DeepSeek models are accessible")
    return {"data": result}
