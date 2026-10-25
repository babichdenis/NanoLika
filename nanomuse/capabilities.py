"""Что умеет эта сборка - одним списком, сгенерированным из неё самой.

Задача файла: чтобы ни одна модель не начала строить то, что уже есть. Ровно это случилось
с почтой - коннектор был, а сказать об этом было негде.

Инвентарь собирается из живого реестра инструментов и настроек
(:func:`inventory`), поэтому он не может разойтись с кодом: инструмент убрали - строка
исчезла, коннектор включили - появилась. Дальше он рендерится в двух видах:

* :func:`render_prompt` - компактный раздел системного промпта: имя, одна фраза, что умеет;
* :func:`render_markdown` - полный файл ``capabilities.md`` для людей и для агентов, которые
  читают файлы (dsh, Claude Code, opencode, Codex): тот же список, но с путями и оговорками.
"""

from __future__ import annotations

from typing import Any

from nanomuse.schema import RiskLevel

#: Группы в том порядке, в котором о них стоит думать: сначала то, о чём просят чаще всего.
GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "Mail",
        "почта",
        ("read_emails", "send_email"),
    ),
    (
        "Calendar and contacts",
        "календарь и контакты",
        ("calendar", "contacts"),
    ),
    (
        "Files and documents",
        "файлы и документы",
        ("files", "office_convert", "office_vba"),
    ),
    (
        "Web",
        "интернет",
        ("web_search", "web_fetch", "browser"),
    ),
    (
        "This computer",
        "этот компьютер",
        ("shell", "python_execute", "computer_screen", "computer_act", "computer_task"),
    ),
    (
        "Other devices",
        "другие устройства",
        (
            "devices",
            "delegate",
            "device_files",
            "device_shell",
            "device_screen",
            "device_notify",
            "device_open",
            "device_get",
            "device_put",
        ),
    ),
    (
        "The phone",
        "телефон",
        ("phone_screen", "phone_act", "phone_task"),
    ),
    (
        "Memory, goals, time",
        "память, цели, время",
        ("remember", "recall", "forget", "goals", "reminders", "triggers"),
    ),
    (
        "Skills, store, bots",
        "навыки, склад, боты",
        ("skills", "store", "bot_projects", "coding_agents", "mcp"),
    ),
)

#: Что сказать про коннектор, которого нет: где переключатель, а не «не умею».
CONNECTOR_SWITCHES = {
    "mail": "Settings → Connectors → Mail (адрес и пароль приложения)",
    "calendar": "Settings → Connectors → Calendar (ссылки на календари)",
    "contacts": "Settings → Connectors → Contacts",
    "browser": "Settings → Browser",
    "hands": "Settings → Hands on this computer",
}


def _one_line(text: str) -> str:
    """Первое предложение описания инструмента - этого хватает, чтобы не путать его с другим."""
    flat = " ".join((text or "").split())
    if not flat:
        return ""
    for stop in (". ", "! ", "? "):
        head, sep, _ = flat.partition(stop)
        if sep and len(head) > 20:
            return head + "."
    return flat if len(flat) <= 220 else flat[:217].rstrip() + "…"


def _tool_line(tool: Any) -> str:
    name = getattr(tool, "name", "")
    desc = _one_line(getattr(tool, "description", ""))
    risk = getattr(tool, "risk", RiskLevel.SAFE)
    mark = " (needs the user's yes)" if str(getattr(risk, "value", risk)) == "sensitive" else ""
    return f"`{name}`{mark}: {desc}" if desc else f"`{name}`{mark}"


def inventory(tools: Any, settings: Any) -> dict[str, Any]:
    """Что есть в этой сборке: группы инструментов, состояние коннекторов, чем закрывать дыры.

    ``tools`` - всё, что умеет ``ToolCollection`` (итерируется, у каждого есть ``name``);
    ``settings`` - :class:`nanomuse.config.Settings`, из них берётся, включён ли коннектор.
    """
    by_name = {getattr(t, "name", ""): t for t in tools}
    groups: list[dict[str, Any]] = []
    listed: set[str] = set()
    for title, russian, names in GROUPS:
        present = [by_name[n] for n in names if n in by_name]
        if not present:
            continue
        listed.update(t.name for t in present)
        groups.append(
            {
                "title": title,
                "russian": russian,
                "tools": [{"name": t.name, "line": _tool_line(t)} for t in present],
            }
        )
    rest = [t for name, t in by_name.items() if name not in listed]
    if rest:
        groups.append(
            {
                "title": "Everything else",
                "russian": "остальное",
                "tools": [{"name": t.name, "line": _tool_line(t)} for t in sorted(rest, key=lambda t: t.name)],
            }
        )

    email = settings.connectors.email
    calendar = settings.connectors.calendar
    contacts = settings.connectors.contacts
    state = {
        "mail": bool(email.enabled and email.imap_host and "read_emails" in by_name),
        "calendar": bool(calendar.enabled and "calendar" in by_name),
        "contacts": bool(contacts.enabled and "contacts" in by_name),
        "browser": "browser" in by_name,
        "hands": "computer_task" in by_name,
        "store": "store" in by_name,
        "phone": "phone_task" in by_name,
    }
    return {"groups": groups, "state": state}


def missing_connectors(state: dict[str, bool]) -> list[str]:
    """Чего нет из того, о чём просят чаще всего, - вместе с тем, где переключатель."""
    return [name for name in ("mail", "calendar", "contacts") if not state.get(name)]


def render_prompt(data: dict[str, Any]) -> str:
    """Компактный раздел промпта: группы, имена и по одной фразе."""
    lines: list[str] = []
    for group in data["groups"]:
        lines.append(f"**{group['title']}** ({group['russian']})")
        lines.extend(f"  - {tool['line']}" for tool in group["tools"])
    text = "\n".join(lines)
    state = data["state"]
    off = missing_connectors(state)
    if off:
        switches = "; ".join(f"{name}: {CONNECTOR_SWITCHES[name]}" for name in off)
        text += (
            "\n\nNot connected in this build: "
            + ", ".join(off)
            + f". The switch is in the app - {switches}. Say that instead of writing a client "
            "of your own or looking for an MCP server: the connector is part of the app."
        )
    return text


def render_markdown(data: dict[str, Any], *, product: str = "NanoLika") -> str:
    """Полный файл для агентов, которые читают файлы, и для людей."""
    state = data["state"]
    on = [name for name, value in state.items() if value]
    off = [name for name, value in state.items() if not value]
    lines = [
        f"# Что умеет {product}: инвентарь сборки",
        "",
        "Сгенерировано из живого реестра инструментов - править руками бессмысленно, файл",
        "перезаписывается. Задача: чтобы ни один агент не строил заново то, что уже есть.",
        "",
        "**Есть:** " + (", ".join(on) or "-") + ".",
    ]
    if off:
        lines += [
            "**Выключено:** " + ", ".join(off) + ".",
            "",
            "| чего нет | где включить |",
            "|---|---|",
        ]
        for name in off:
            lines.append(f"| {name} | {CONNECTOR_SWITCHES.get(name, 'в настройках приложения')} |")
    lines.append("")
    for group in data["groups"]:
        lines += [f"## {group['title']} - {group['russian']}", ""]
        lines += [f"- {tool['line']}" for tool in group["tools"]]
        lines.append("")
    lines += [
        "## Правила",
        "",
        "- Сначала посмотри в этот список, потом в `store` (`action=list`), и только потом "
        "что-то строй.",
        "- `pip install` в замороженном рантайме не работает: код добавляется только со склада.",
        "- MCP-сервер - это для того, чего нет ни в сборке, ни на складе, а не вместо "
        "встроенного коннектора.",
        "",
    ]
    return "\n".join(lines)


__all__ = [
    "CONNECTOR_SWITCHES",
    "GROUPS",
    "inventory",
    "missing_connectors",
    "render_markdown",
    "render_prompt",
]
