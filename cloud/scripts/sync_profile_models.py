#!/usr/bin/env python3
"""Разливка меню релея в профиль приложения.

Селектор моделей в dsh собирается ядром из провайдеров (`buildModelCatalog`:
`ctx.llm.listProviders()` + `listModels()`), а провайдеры даёт плагин
`@deepseek-ai/dsh-llm-pi-ai`, читая секцию `providers` из профиля
(`cordis.patch.yml`) и из настроек, которые пишет страница «Модели». Наш
релей — это провайдер `nanomuse` в этой же секции, со статическим списком
моделей. Список руками не поддерживается: этот скрипт пересобирает его из
`GET /v1/menu` (публичного, без ключа), сохраняя поля, которых в меню нет
(`contextWindow`, `maxTokens` и любые другие, что уже стоят у модели).

    # что изменится (ничего не пишет)
    .venvs/nm/bin/python cloud/scripts/sync_profile_models.py --check
    # переписать профиль (рядом ляжет .bak-sync-<время>)
    .venvs/nm/bin/python cloud/scripts/sync_profile_models.py --write

Смысл: одна истина — меню релея. Профиль — его копия, и копия обновляема.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import shutil
import ssl
import sys
import urllib.request

ROW = "nanomuse"  # роут нашего релея в секции providers
PLUGIN = "llm-pi-ai"
INDENT_PROVIDER = "      "  # `      nanomuse:` внутри `- id: llm-pi-ai`
INDENT_MODELS = "        "  # `        models:`
INDENT_MODEL = "          "  # `          - id: ...`


def default_profile() -> pathlib.Path:
    return pathlib.Path.home() / ".nanomuse/desktop/profiles/nanolika/cordis.patch.yml"


def fetch_menu(base: str, timeout: float = 30.0) -> dict:
    url = base.rstrip("/") + "/v1/menu"
    # системное хранилище сертификатов на macOS пустое для python.org сборок,
    # поэтому берём корни из certifi, когда он есть, и молча откатываемся, когда нет
    try:
        import certifi

        context = ssl.create_default_context(cafile=certifi.where())
    except ModuleNotFoundError:
        context = ssl.create_default_context()
    request = urllib.request.Request(url, headers={"User-Agent": "nanomuse-profile-sync"})
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:  # noqa: S310 - адрес наш
        return json.loads(response.read().decode("utf-8"))


def wanted(menu: dict, kinds: tuple[str, ...]) -> list[dict]:
    """Модели меню нужного вида, в порядке меню."""
    return [m for m in menu.get("models") or [] if m.get("kind") in kinds]


def modulo(words: list[str]) -> list[str]:
    """Модальности меню в терминах pi-ai: только то, что он умеет объявить."""
    return [w for w in words if w in ("text", "image")]


def scalar(value: str) -> str:
    """Скаляр YAML: как есть, если это безопасно, иначе в кавычках."""
    if re.match(r"^[A-Za-z0-9][A-Za-z0-9 ._+()/\-]*$", value) and ": " not in value:
        return value
    return json.dumps(value, ensure_ascii=False)


def read_models_from_text(text: str) -> dict[str, dict]:
    """Что сейчас стоит у моделей нашего роута в профиле: id -> запись.

    Полей в меню нет (`contextWindow`, `maxTokens`, будущие), поэтому берём
    их из профиля как есть и переносим в новую сборку. Разбор нарочно
    текстовый: переписывать YAML целиком (и терять комментарии профиля)
    нельзя, а нужный блок — простой список отображений.
    """
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line == f"- id: {PLUGIN}")
    except StopIteration:
        return {}
    block_start = next((i for i in range(start, len(lines)) if lines[i] == f"{INDENT_PROVIDER}{ROW}:"), None)
    if block_start is None:
        return {}
    block_end = len(lines)
    for i in range(block_start + 1, len(lines)):
        line = lines[i]
        if not line.strip():
            continue
        if len(line) - len(line.lstrip()) <= len(INDENT_PROVIDER):
            block_end = i
            break
    found: dict[str, dict] = {}
    current: dict | None = None
    key: str | None = None
    for line in lines[block_start:block_end]:
        match = re.match(rf"^{INDENT_MODEL}- id: (.+)$", line)
        if match:
            current = {"id": match.group(1).strip().strip('"')}
            found[current["id"]] = current
            key = None
            continue
        if current is None:
            continue
        match = re.match(rf"^{INDENT_MODEL}  ([A-Za-z0-9_]+): (.*)$", line)
        if match:
            key = match.group(1)
            raw = match.group(2).strip()
            if raw:
                if raw.startswith('"'):
                    current[key] = json.loads(raw)
                elif re.fullmatch(r"-?\d+", raw):
                    current[key] = int(raw)
                elif raw in ("true", "false"):
                    current[key] = raw == "true"
                else:
                    current[key] = raw
            continue
        match = re.match(rf"^{INDENT_MODEL}  ([A-Za-z0-9_]+):$", line)
        if match:
            key = match.group(1)
            continue
        match = re.match(rf"^{INDENT_MODEL}    - (.+)$", line)
        if match and key:
            bucket = current.get(key)
            if not isinstance(bucket, list):
                bucket = []
                current[key] = bucket
            bucket.append(match.group(1).strip().strip('"'))
    return found


def render_model(model: dict, kept: dict) -> list[str]:
    """Строки одной модели: id, имя, модальности и всё, что было в профиле."""
    name = model.get("name") or model["id"]
    inputs = modulo(list(model.get("in") or [])) or ["text"]
    out = [f"{INDENT_MODEL}- id: {scalar(model['id'])}", f"{INDENT_MODEL}  displayName: {scalar(name)}"]
    out.append(f"{INDENT_MODEL}  input:")
    out.extend(f"{INDENT_MODEL}    - {item}" for item in inputs)
    for key, value in kept.items():
        if key in ("id", "displayName", "input"):
            continue
        if isinstance(value, list):
            out.append(f"{INDENT_MODEL}  {key}:")
            out.extend(f"{INDENT_MODEL}    - {scalar(str(item))}" for item in value)
        elif isinstance(value, bool):
            out.append(f"{INDENT_MODEL}  {key}: {'true' if value else 'false'}")
        elif isinstance(value, int):
            out.append(f"{INDENT_MODEL}  {key}: {value}")
        else:
            out.append(f"{INDENT_MODEL}  {key}: {scalar(str(value))}")
    return out


def render_block(text: str, models: list[dict]) -> str:
    """Текст профиля с пересобранным блоком моделей нашего роута."""
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line == f"- id: {PLUGIN}")
    except StopIteration as error:
        raise SystemExit(f"в профиле нет `- id: {PLUGIN}`") from error
    block_start = next((i for i in range(start, len(lines)) if lines[i] == f"{INDENT_PROVIDER}{ROW}:"), None)
    if block_start is None:
        raise SystemExit(f"в профиле нет провайдера `{ROW}`")
    block_end = len(lines)
    for i in range(block_start + 1, len(lines)):
        line = lines[i]
        if not line.strip():
            continue
        if len(line) - len(line.lstrip()) <= len(INDENT_PROVIDER):
            block_end = i
            break
    header_end = next((i for i in range(block_start, block_end) if lines[i] == f"{INDENT_MODELS}models:"), None)
    if header_end is None:
        raise SystemExit(f"у провайдера `{ROW}` нет `models:`")
    kept_by_id = read_models_from_text(text)
    body = [f"{INDENT_MODELS}models:"]
    for model in models:
        body.extend(render_model(model, kept_by_id.get(model["id"], {})))
    return "\n".join(lines[:header_end] + body + lines[block_end:]) + "\n"


def report(before: dict[str, dict], models: list[dict]) -> list[str]:
    """Что разливка меняет — по строке на изменение."""
    after = {m["id"]: m for m in models}
    changes: list[str] = []
    for model_id in sorted(set(after) - set(before)):
        changes.append(f"+ {model_id} ({after[model_id].get('name')})")
    for model_id in sorted(set(before) - set(after)):
        changes.append(f"- {model_id}")
    for model_id in sorted(set(before) & set(after)):
        old, new = before[model_id], after[model_id]
        if old.get("displayName") != (new.get("name") or model_id):
            changes.append(f"~ {model_id}: имя «{old.get('displayName')}» → «{new.get('name')}»")
        was = list(old.get("input") or [])
        now = modulo(list(new.get("in") or [])) or ["text"]
        if was != now:
            changes.append(f"~ {model_id}: модальности {was} → {now}")
    return changes


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Разливка /v1/menu релея в профиль приложения")
    parser.add_argument("--profile", type=pathlib.Path, default=default_profile())
    parser.add_argument("--base", default="https://cloud.nanolika.ru")
    parser.add_argument("--kinds", default="chat", help="виды моделей через запятую (по умолчанию chat)")
    parser.add_argument("--check", action="store_true", help="только показать, что изменится")
    parser.add_argument("--write", action="store_true", help="переписать профиль (нужен бэкап-разрешение)")
    args = parser.parse_args(argv)

    profile = args.profile.expanduser()
    if not profile.is_file():
        raise SystemExit(f"нет профиля: {profile}")
    kinds = tuple(k.strip() for k in args.kinds.split(",") if k.strip())
    menu = fetch_menu(args.base)
    models = wanted(menu, kinds)
    if not models:
        raise SystemExit(f"в меню нет моделей вида {kinds}")

    text = profile.read_text(encoding="utf-8")
    before = read_models_from_text(text)
    changes = report(before, models)
    print(f"меню: {len(menu.get('models') or [])} моделей, из них {kinds}: {len(models)}")
    print(f"профиль: {len(before)} моделей у роута `{ROW}`")
    if not changes:
        print("расхождений нет — профиль уже совпадает с меню")
        return 0
    print(f"расхождений: {len(changes)}")
    for line in changes:
        print("  " + line)
    if args.check or not args.write:
        print("ничего не записано (--check; для записи нужен --write)")
        return 0

    new_text = render_block(text, models)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = profile.with_name(f"{profile.name}.bak-sync-{stamp}")
    shutil.copy2(profile, backup)
    profile.write_text(new_text, encoding="utf-8")
    print(f"записано: {profile}")
    print(f"бэкап:   {backup}")
    print("приложение читает профиль при старте — правка подхватится после перезапуска")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
