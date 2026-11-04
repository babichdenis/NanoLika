"""The short list in the request and the catalog behind `tool_search`."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from nanomuse.config import Settings
from nanomuse.tools.discovery import CORE, ToolSearch, score

QUERIES = [
    ("нарисуй кота на скейте", "picture"),
    ("сделай короткий клип с закатом", "video"),
    ("озвучь этот текст женским голосом", "media"),
    ("расшифруй голосовое сообщение", "media"),
    ("поставь напоминание через 5 минут", "reminders"),
    ("какие у меня цели", "goals"),
    ("что ты помнишь обо мне", "recall"),
    ("запомни, что я не ем мясо", "remember"),
    ("поищи в интернете курс доллара", "web_search"),
    ("прочитай страницу по ссылке", "web_fetch"),
    ("прочитай файл budget.csv", "files"),
    ("посчитай на python сколько будет 1234*56", "python_execute"),
    ("запусти git status", "shell"),
    ("посмотри, что у меня на экране", "computer_screen"),
    ("кликни по кнопке Сохранить", "computer_act"),
    ("сделай Excel с макросом и кнопкой", "office_xlsm"),
    ("переведи docx в pdf", "office_convert"),
    ("что у меня в календаре на завтра", "calendar"),
    ("проверь почту", "read_emails"),
    ("найди контакт Маши", "contacts"),
    ("создай телеграм-бота", "bot_projects"),
    ("сколько стоит картинка 1024x1024", "models"),
    ("какие инструменты ты умеешь", "skills"),
    ("останови работу", "terminate"),
]


def built(settings: Settings, **agent):
    from nanomuse.app import NanoMuseApp
    from nanomuse.ui import HeadlessUI

    merged = {**settings.agent.model_dump(), "tool_search": True, **agent}
    settings = settings.model_copy(update={"agent": settings.agent.model_validate(merged)})
    app = NanoMuseApp(settings, HeadlessUI())
    return app, app._build_tools()


def test_the_request_carries_the_core_and_the_search_but_not_the_rest(settings: Settings):
    _app, tools = built(settings)
    shown = {t["function"]["name"] for t in tools.to_params()}
    assert "tool_search" in shown
    assert set(CORE) & set(tools.tool_map) <= shown
    assert "picture" not in shown and "video" not in shown and "office_xlsm" not in shown
    # every hidden tool is still callable by name: the model learns it from the search
    assert tools.get("picture") is not None
    assert tools.visible is not None and "picture" not in tools.visible


def test_the_list_is_much_smaller_than_the_whole_catalog(settings: Settings):
    _app, tools = built(settings)
    full = len(json.dumps([t.to_param() for t in tools.tools], ensure_ascii=False))
    short = len(json.dumps(tools.to_params(), ensure_ascii=False))
    assert short < full * 0.55, f"the short list is {short} of {full} bytes"


def test_the_search_finds_the_tool_a_russian_question_means(settings: Settings):
    """Words alone do not cross languages; the hand-written words do. A tool the search
    cannot name in five is a tool the model will say it does not have."""
    _app, tools = built(settings)
    hidden = set(tools.tool_map) - set(CORE)
    search = ToolSearch.over(list(tools.tools), hidden)
    hit5 = 0
    misses = []
    asked = [(q, w) for q, w in QUERIES if w in hidden]  # the core is in the prompt anyway
    for query, want in asked:
        got = asyncio.run(search.execute(query=query, limit=5)).output
        if f"- {want}:" in got:
            hit5 += 1
        else:
            misses.append((query, want))
    assert hit5 >= 0.9 * len(asked), f"{hit5}/{len(asked)} in the top five; missed {misses}"
    # a core tool is not in the catalog: the prompt already carries it
    assert "files" not in search.catalog


def test_a_search_returns_the_schema_so_the_call_follows_at_once(settings: Settings):
    _app, tools = built(settings)
    hidden = set(tools.tool_map) - set(CORE)
    search = ToolSearch.over(list(tools.tools), hidden)
    out = asyncio.run(search.execute(query="нарисуй кота")).output
    assert "- picture:" in out and '"type": "object"' in out and "prompt" in out


def test_a_query_that_matches_nothing_says_what_there_is(settings: Settings):
    _app, tools = built(settings)
    hidden = set(tools.tool_map) - set(CORE)
    search = ToolSearch.over(list(tools.tools), hidden)
    out = asyncio.run(search.execute(query="квазар")).output
    assert "Nothing matched" in out and "picture" in out


def test_the_prompt_names_the_tools_it_carries_and_the_search(settings: Settings):
    from test_agent import make_agent

    app, tools = built(settings)
    agent, *_ = make_agent(app.settings, [])
    agent.tools = tools
    names = agent._tool_names()
    assert "files" in names and "tool_search" in names
    assert "picture" not in names.split(";")[0]
    assert "more are not described here" in names


def test_a_build_without_the_switch_sends_everything(settings: Settings):
    from nanomuse.app import NanoMuseApp
    from nanomuse.ui import HeadlessUI

    app = NanoMuseApp(settings, HeadlessUI())
    tools = app._build_tools()
    assert tools.visible is None
    assert {"picture", "files"} <= {t["function"]["name"] for t in tools.to_params()}
    assert score("нарисуй картинку", tools.get("picture")) > 0
