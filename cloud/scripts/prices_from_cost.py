#!/usr/bin/env python3
"""Розничные ставки из закупки: одна базовая цена на модель, скидки тарифов сверху.

Требование владельца (25.10.2026): при скидке 30 % (тариф «Команда») остаётся 100 %
прибыли, то есть клиент платит вдвое больше закупки. Значит базовая ставка:

    sell = закупка × 2 / (1 − 0.30) = 2.857 × закупка

Тогда:

| тариф | скидка | клиент платит | прибыль |
|---|---|---|---|
| пополнение без тарифа | 0 %  | 2.857 × закупка | 186 % |
| Лика, 590 ₽          | 20 % | 2.286 × закупка | 129 % |
| Лика+, 1490 ₽        | 20 % | 2.286 × закупка | 129 % |
| Команда, 3900 ₽      | 30 % | 2.000 × закупка | 100 % |

Кеш-вход продаётся как десятая часть входа: у DeepSeek кеш и закупается по 0.1 от входа.

Запуск на сервере (файл с моделями — CLOUD_MODELS в docker-compose.override.yml):

    python3 prices_from_cost.py --dry          # показать, ничего не менять
    python3 prices_from_cost.py --apply        # записать (рядом ляжет .bak-prices-<время>)

Скрипт не трогает закупочные поля (`price_in`, `price_out`, `price_in_idle`, ...) и не
меняет состав меню: только `sell_in`, `sell_cached_in`, `sell_out` у chat-моделей.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import shutil
import sys

MARKUP = 2.0 / (1.0 - 0.30)  # 2.857: 100 % прибыли при скидке 30 %
CACHED_SHARE = 0.1


def cny_to_rub(path: pathlib.Path) -> float:
    """Сколько рублей стоит один юань закупки: тот же путь, что у релея.

    ₽ = ¥ / USD_CNY × RUB_PER_USD × (1 + TOPUP_FEE_PCT / 100). Без курсов скрипт
    отказывается считать: иначе продажная цена вышла бы в юанях под видом рублей.
    """
    env = path.parent / ".env"
    values: dict[str, str] = {}
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"')
    rub_per_usd = float(values.get("RUB_PER_USD") or 0)
    usd_cny = float(values.get("USD_CNY") or 0)
    fee = float(values.get("TOPUP_FEE_PCT") or 0)
    if rub_per_usd <= 0 or usd_cny <= 0:
        raise SystemExit("в .env нет рабочих RUB_PER_USD и USD_CNY: считать нечем")
    return round(rub_per_usd / usd_cny * (1 + fee / 100), 4)


CACHED_SHARE = 0.1


def sell_for(price_in: float, price_out: float, rate: float) -> tuple[float, float, float]:
    """Базовая ставка в рублях: вход, кеш-вход, выход (за миллион токенов).

    Закупка в конфиге в юанях, поэтому сначала переводим её в рубли тем же курсом,
    что у релея, и только потом умножаем на наценку.
    """
    rub_in = price_in * rate
    rub_out = (price_out or price_in) * rate
    sell_in = round(rub_in * MARKUP, 2)
    sell_out = round(rub_out * MARKUP, 2)
    sell_cached = round(sell_in * CACHED_SHARE, 2)
    return sell_in, sell_cached, sell_out


def models_from_file(path: pathlib.Path) -> tuple[list[dict], str]:
    """Модели из compose-override: значение читает YAML, дальше это обычный JSON."""
    text = path.read_text(encoding="utf-8")
    try:
        import yaml
    except ModuleNotFoundError as error:  # pragma: no cover - есть на сервере
        raise SystemExit(f"нужен PyYAML: {error}") from error
    data = yaml.safe_load(text) or {}
    env = ((data.get("services") or {}).get("relay") or {}).get("environment") or {}
    raw = env.get("CLOUD_MODELS")
    if not raw:
        raise SystemExit("в services.relay.environment нет CLOUD_MODELS")
    return json.loads(raw), text


def rewrite_models(text: str, models: list[dict]) -> str:
    """Заменить блок CLOUD_MODELS одной строкой с тем же отступом."""
    payload = json.dumps(models, ensure_ascii=False, separators=(", ", ": ")).replace('"', '\\"')
    pattern = re.compile(r"^(\s*)CLOUD_MODELS:.*?(?=^\s*[A-Z_]+:|\Z)", re.M | re.S)
    match = pattern.search(text)
    if not match:
        raise SystemExit("не нашёл строку CLOUD_MODELS для замены")
    indent = match.group(1)
    return text[: match.start()] + f'{indent}CLOUD_MODELS: "{payload}"\n' + text[match.end() :]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Розница из закупки")
    parser.add_argument("--file", default="/opt/nanomuse/cloud/docker-compose.override.yml")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args(argv)

    path = pathlib.Path(args.file)
    models, text = models_from_file(path)
    rate = cny_to_rub(path)
    print(f"курс закупки: 1 ¥ = {rate} ₽; наценка ×{MARKUP:.3f}")

    changed = 0
    rows = []
    for model in models:
        if model.get("kind", "chat") != "chat":
            continue
        price_in = float(model.get("price_in") or 0)
        price_out = float(model.get("price_out") or 0)
        if not price_in and not price_out:
            continue
        sell_in, sell_cached, sell_out = sell_for(price_in, price_out, rate)
        old = (model.get("sell_in"), model.get("sell_cached_in"), model.get("sell_out"))
        if old != (sell_in, sell_cached, sell_out):
            changed += 1
        model["sell_in"] = sell_in
        model["sell_cached_in"] = sell_cached
        model["sell_out"] = sell_out
        rows.append((model.get("id"), price_in, sell_in, price_out, sell_out))

    print(f"моделей в меню: {len(models)}; chat с закупкой: {len(rows)}; изменится: {changed}")
    print(f"{'модель':38} {'вход ¥':>8} {'вход ₽':>9} {'выход ¥':>9} {'выход ₽':>10}")
    for model_id, price_in, sell_in, price_out, sell_out in rows[:15]:
        print(f"{model_id:38} {price_in:8.3f} {sell_in:9.2f} {price_out:9.3f} {sell_out:10.2f}")
    if len(rows) > 15:
        print(f"... и ещё {len(rows) - 15}")

    if not args.apply:
        print("ничего не записано (--dry; для записи нужен --apply)")
        return 0

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bak-prices-{stamp}")
    shutil.copy2(path, backup)
    path.write_text(rewrite_models(text, models), encoding="utf-8")
    print(f"записано: {path}")
    print(f"бэкап:   {backup}")
    print("перезапустить релей: docker compose up -d relay")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
