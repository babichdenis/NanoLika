#!/usr/bin/env python3
"""Прайсы вендоров, которые нельзя снять машинно: российские API за капчей и JS.

Почему файл существует. Сбер (GigaChat) отдаёт тарифы только JS-приложением, а Яндекс
(AI Studio) — за SmartCaptcha: `curl` и headless-браузер получают «Подтвердите, что запросы
отправляли вы, а не робот». Ни `models.dev`, ни LiteLLM российских вендоров не знают. Поэтому
здесь **ручной прайс**: цифра, когда её видели, где именно, и ссылка. Он попадает в каталог
наравне с автоматическими, и по нему считается «дешевле напрямую, чем у перепродавца».

Правило обновления: увидел новую цену на странице вендора — поправил строку, поставил дату.
Пустая цена (`None`) значит «не снято», а не «бесплатно»; такие строки в каталог не едут, но
вендор со ссылкой остаётся в списке источников.

    python3 cloud/scripts/vendor_prices.py             # что заполнено, что нет
    python3 cloud/scripts/vendor_prices.py --json      # JSON для каталога
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

#: Рублёвые цены за 1M токенов. `match` — по какому куску id модели узнавать её у продавца.
#: `checked` — ISO-дата, когда цифру видели на странице вендора своими глазами.
VENDORS: list[dict[str, Any]] = [
    {
        "vendor": "Sber",
        "brand": "GigaChat",
        "docs": "https://developers.sber.ru/docs/ru/gigachat/api/tariffs",
        "models": [
            {"match": "gigachat-2-lite", "name": "GigaChat 2 Lite", "in": None, "out": None},
            {"match": "gigachat-2-pro", "name": "GigaChat 2 Pro", "in": None, "out": None},
            {"match": "gigachat-2-max", "name": "GigaChat 2 Max", "in": None, "out": None},
            {"match": "gigachat-3.5-ultra", "name": "GigaChat 3 Ultra", "in": None, "out": None},
            {"match": "gigachat-plus", "name": "GigaChat Plus", "in": None, "out": None},
            {"match": "gigachat-pro", "name": "GigaChat Pro", "in": None, "out": None},
            {"match": "gigachat-max", "name": "GigaChat Max", "in": None, "out": None},
        ],
    },
    {
        "vendor": "Yandex",
        "brand": "YandexGPT / AI Studio",
        "docs": "https://yandex.cloud/ru/docs/ai-studio/pricing",
        "models": [
            {"match": "yandexgpt-5-pro", "name": "YandexGPT 5 Pro", "in": None, "out": None},
            {"match": "yandexgpt-5-lite", "name": "YandexGPT 5 Lite", "in": None, "out": None},
            {"match": "gpt-pro-5.1", "name": "YandexGPT Pro 5.1", "in": None, "out": None},
            {"match": "aliceai-llm", "name": "Alice AI LLM", "in": None, "out": None},
        ],
    },
    {
        "vendor": "T-Bank",
        "brand": "T-lite / T-pro",
        "docs": "https://www.tbank.ru/business/ai/",
        "models": [
            {"match": "drex-v1.5", "name": "T-Bank drex 1.5", "in": None, "out": None},
            {"match": "drex-v2.0", "name": "T-Bank drex 2.0", "in": None, "out": None},
            {"match": "drex-max", "name": "T-Bank drex Max", "in": None, "out": None},
        ],
    },
]

#: Вендоры, чей прайс читается машинно: для них в каталоге есть и цена, и ссылка.
AUTOMATIC = {
    "DeepSeek": "https://api-docs.deepseek.com/quick_start/pricing/",
    "OpenAI": "https://openai.com/api/pricing/",
    "Anthropic": "https://www.anthropic.com/pricing",
    "Google": "https://ai.google.dev/gemini-api/docs/pricing",
    "xAI": "https://docs.x.ai/docs/models",
    "Moonshot": "https://platform.moonshot.ai/docs/pricing",
    "Z.ai": "https://docs.z.ai/guides/overview/pricing",
}


def payload() -> dict[str, Any]:
    """Вендоры и их заполненные цены; пустые строки остаются в списке, но без цифр."""
    manual: list[dict[str, Any]] = []
    vendors: list[dict[str, Any]] = []
    for vendor in VENDORS:
        models = []
        for model in vendor["models"]:
            entry = {
                "match": model["match"],
                "name": model["name"],
                "in": model.get("in"),
                "out": model.get("out"),
                "unit": model.get("unit"),
                "checked": model.get("checked"),
                "vendor": vendor["vendor"],
                "brand": vendor["brand"],
                "source": vendor["docs"],
            }
            models.append(entry)
            if entry["in"] is not None or entry["out"] is not None or entry["unit"]:
                manual.append(entry)
        vendors.append(
            {
                "vendor": vendor["vendor"],
                "brand": vendor["brand"],
                "docs": vendor["docs"],
                "filled": sum(1 for m in models if m["in"] is not None or m["out"] is not None),
                "total": len(models),
            }
        )
    return {"vendors": vendors, "manual": manual, "automatic": AUTOMATIC}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="прайсы вендоров, снятые руками")
    parser.add_argument("--json", action="store_true", help="напечатать JSON")
    args = parser.parse_args(argv)
    data = payload()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return 0
    for vendor in data["vendors"]:
        print(f"{vendor['vendor']:10} {vendor['brand']:24} {vendor['filled']}/{vendor['total']}  {vendor['docs']}")
    print(f"\nзаполнено цен: {len(data['manual'])}")
    empty = sum(v["total"] - v["filled"] for v in data["vendors"])
    if empty:
        print(
            f"не заполнено: {empty}. Страницы вендоров за капчей/JS — цифру вписать руками в cloud/scripts/vendor_prices.py, с датой.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
