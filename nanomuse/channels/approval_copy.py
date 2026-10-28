"""Plain-language permission cards. No model call, no guessed safe verdict."""

from __future__ import annotations

import re
from typing import Any


def clean(value: Any, limit: int = 400) -> str:
    text = str(value or "")
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", text)
    text = re.sub(r"(?i)(bearer\s+)\S+", r"\1[скрыто]", text)
    text = re.sub(
        r"(?i)((?:password|passwd|token|api[_-]?key|secret)\s*[=:]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
        r"\1[скрыто]",
        text,
    )
    text = re.sub(r"https?://[^\s/@]+:[^\s/@]+@", "https://[скрыто]@", text)
    text = re.sub(r"\b[A-Za-z0-9_+/=-]{32,}\b", "[длинное значение скрыто]", text)
    return text[:limit] + ("…" if len(text) > limit else "")


def readable_approval(event: dict[str, Any], name: str) -> str:
    tool = str(event.get("tool") or "")
    args = event.get("args") if isinstance(event.get("args"), dict) else {}
    actions = {
        "shell": "Выполнить команду: запустить программы или технические операции.",
        "device_shell": "Выполнить команду на связанном устройстве.",
        "read_file": "Прочитать файл.",
        "write_file": "Записать файл.",
        "edit_file": "Изменить содержимое файла.",
        "list_dir": "Посмотреть содержимое папки.",
        "device_files": "Посмотреть файлы на связанном устройстве.",
        "device_notify": "Показать уведомление на связанном устройстве.",
        "device_open": "Открыть ссылку на связанном устройстве.",
        "device_screen": "Посмотреть экран связанного устройства.",
        "send_email": "Отправить письмо.",
        "browser": "Выполнить действие в браузере.",
        "computer": "Выполнить действие на компьютере.",
        "delegate": "Передать задачу Лике на другом устройстве.",
    }
    lines = [
        f"{clean(name, 50)} просит разрешение",
        "",
        "Что сделать: "
        + actions.get(tool, "Выполнить действие с помощью подключённого инструмента."),
    ]
    purpose = clean(event.get("purpose"), 260).strip()
    if purpose:
        lines += ["", "В рамках вашей задачи: «" + purpose + "»"]
    if tool in {"shell", "device_shell"}:
        command = str(args.get("command") or "")
        operations = []
        for pattern, label in [
            (r"\bgit\b", "работа с Git"),
            (r"\b(npm|pnpm|tsc|pytest|ruff)\b", "сборка или проверка кода"),
            (r"\bdocker\b", "работа с контейнерами"),
            (r"\b(systemctl|service)\b", "управление службами"),
            (r"\b(curl|wget|ssh|scp)\b", "сетевые обращения"),
        ]:
            if re.search(pattern, command):
                operations.append(label)
        if operations:
            lines += ["В команде: " + "; ".join(operations) + "."]
        lines += [
            "",
            "Важно: команда может менять файлы, запускать программы или обращаться к сети.",
        ]
    elif tool in {"write_file", "edit_file"}:
        lines += ["", "Существующее содержимое файла может измениться."]
    for key, label in [("path", "Файл или папка"), ("to", "Получатель"), ("url", "Ссылка")]:
        if args.get(key) and isinstance(args[key], (str, int)):
            lines += [label + ": " + clean(args[key], 180)]
    warnings = event.get("warnings") or []
    if warnings:
        lines += [
            "",
            "⚠️ Обнаружены дополнительные риски. Перед разрешением откройте «Подробности».",
        ]
    scopes = event.get("grant_options") or ["once"]
    if "conversation" in scopes:
        lines += [
            "",
            "«Для этой беседы»: запомнить разрешение для этого типа действий в текущем разговоре.",
        ]
    if "always" in scopes:
        lines += ["«Всегда»: запомнить до отмены, в пределах указанного действия и цели."]
    if scopes == ["once"]:
        lines += ["", "Разрешение действует только на этот запуск."]
    return "\n".join(lines)


def approval_details(event: dict[str, Any]) -> str:
    args = event.get("args") if isinstance(event.get("args"), dict) else {}
    lines = [
        "Технические подробности запроса",
        "Инструмент: " + clean(event.get("tool"), 100),
        "Описание: " + clean(event.get("summary"), 350),
    ]
    for key in ("device", "command", "path", "url", "to", "target", "timeout"):
        if key in args:
            lines += [key + ": " + clean(args[key], 1800 if key == "command" else 200)]
    if event.get("target"):
        lines += ["Цель разрешения: " + clean(event["target"], 200)]
    for warning in (event.get("warnings") or [])[:4]:
        lines += ["Предупреждение: " + clean(warning, 200)]
    lines += [
        "",
        "Это только просмотр подробностей. Разрешение ещё не выдано. Секретные и слишком длинные значения могут быть скрыты; для них проверьте исходную команду в приложении или рабочем проекте.",
    ]
    return "\n".join(lines)[:3900]
