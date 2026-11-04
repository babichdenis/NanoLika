"""Finding a tool the prompt does not carry in full.

A long catalog is expensive: the schemas of thirty tools weigh more than the whole system
prompt, and most of them are not used in a given turn. So the prompt keeps a short core
(the tools without which nothing works) and the rest are found by asking `tool_search`:
a name, one line, and the full input schema of the best few matches, so the call follows
the search without a second round trip.

This is the idea of mcp-toolsearch-proxy (datolabs, MIT) applied inside NanoLika: no
proxy and no MCP, just a smaller list in the request and the same five facts — the name,
what it does, and the schema when it matters.

The words below are the bridge between the two languages: tools describe themselves in
English, people ask in Russian, and word matching alone cannot cross that. They are
written by hand, which is honest for a catalog we own, and cheap: no model, no download.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from pydantic import Field

from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment

#: What a person says when they want this job, in Russian (and a few English synonyms).
#: Matching ignores case, ё and word endings; a tool missing here is still found by its
#: own name and description, only worse.
WORDS: dict[str, str] = {
    "picture": "картинка рисунок нарисовать изображение фото фотография арт иллюстрация открытка постер "
    "draw image picture",
    "video": "видео клип ролик анимация снять снимать movie clip",
    "media": "озвучить озвучка голос речь сказать вслух аудио звук музыка песня трек расшифровать "
    "транскрипция запись voice speech audio transcribe music",
    "models": "модели список моделей цена стоимость сколько стоит тариф модели модели ИИ llm model price cost",
    "office_docx": "документ docx ворд word отчёт доклад письмо резюме договор",
    "office_xlsx": "таблица excel эксель книга смета бюджет расчёт xlsx spreadsheet",
    "office_pptx": "презентация слайды pptx powerpoint колода deck slides",
    "office_xlsm": "excel макрос кнопка xlsm vba макрос excel",
    "office_convert": "конвертировать перевести pdf docx xlsx pptx формат",
    "office_vba": "vba макрос вставить макрос word excel powerpoint",
    "files": "файл файлы прочитать записать сохранить папка каталог file read write save folder",
    "shell": "команда терминал shell bash консоль запустить команду git",
    "python_execute": "python питон код скрипт посчитать вычисления script",
    "web_search": "поиск в интернете найти в интернете гугл搜索引擎 search web google",
    "web_fetch": "страница ссылка сайт открыть ссылку url fetch page",
    "goals": "цель цели план задачи долгосрочно goal plan",
    "reminders": "напоминание напомнить таймер будильник через минуту reminder timer",
    "triggers": "триггер событие когда произойдёт подписка на событие trigger",
    "remember": "запомнить запомни запомни это remember",
    "recall": "вспомнить что помнишь память поиск по памяти remember memory",
    "forget": "забыть удалить из памяти forget",
    "contacts": "контакт контакты адрес книга телефон человек contact address book",
    "read_emails": "почта письма входящие проверить почту email inbox mail",
    "send_email": "отправить письмо написать письмо email send mail",
    "calendar": "календарь встреча событие расписание на завтра calendar event",
    "skills": "навык навыки инструкция рецепт skill",
    "bot_projects": "бот телеграм бот канал max бот создать бота bot telegram",
    "computer_screen": "экран посмотри на экран скриншот что на экране screenshot screen",
    "computer_act": "кликнуть клик нажать кнопка мышь ввод экран click mouse",
    "computer_task": "сделай на компьютере поручи компьютеру открой программу computer task",
    "browser": "браузер сайт вкладка страница browser",
    "phone_act": "телефон телефон приложение нажми на телефоне phone",
    "phone_screen": "экран телефона посмотри телефон phone screen",
    "store": "склад установить расширение store addon",
    "ask_user": "спросить уточнить вопрос ask question",
    "terminate": "закончить остановиться готово finish stop done",
}

#: Without these the agent cannot work at all: they stay in the prompt whatever the
#: catalog size. Everything else is found by `tool_search`.
CORE: tuple[str, ...] = (
    "files",
    "shell",
    "python_execute",
    "web_search",
    "web_fetch",
    "skills",
    "ask_user",
    "terminate",
)

#: the tool's name, for a caller that cannot read a pydantic field off the class
TOOL_SEARCH = "tool_search"

#: how many leading letters of a stem decide that two words are the same one
_STEM = 4

#: endings worth cutting: Russian cases and plurals, English inflection
_ENDING = (
    "ыми", "ими", "ого", "его", "ому", "ему", "ами", "ями", "ов", "ев", "ам", "ям",
    "ах", "ях", "ом", "ем", "ой", "ей", "ую", "юю", "ая", "яя", "ое", "ее", "ые",
    "ий", "ый", "а", "я", "ы", "и", "у", "ю", "е", "о", "ь", "й",
    "ing", "ed", "es", "s",
)

#: words that say nothing about a job
_STOP = frozenset(
    """что как мне меня мой моя мои у в на и а по за для с к из от до не это этот эта
    тебя себя нам вам они он она оно the a an to me my i is are can you do does of for
    with on in""".split()
)


def words(text: str) -> list[str]:
    """Lower-case stems of the content words of a text: ё folded to е, punctuation and
    the words that say nothing dropped, endings cut."""
    folded = (text or "").lower().replace("ё", "е")
    out = []
    for w in re.split(r"[^0-9a-zа-я]+", folded):
        if len(w) < 3 or w in _STOP:
            continue
        out.append(stem(w))
    return out


def stem(word: str) -> str:
    """A crude stem: enough for matching a question to a tool's words, not a stemmer."""
    w = word
    changed = True
    while changed and len(w) > 3:
        changed = False
        for ending in _ENDING:
            if w.endswith(ending) and len(w) - len(ending) >= 3:
                w = w[: -len(ending)]
                changed = True
                break
    return w


def _same(a: str, b: str) -> bool:
    if a == b:
        return True
    return len(a) >= _STEM and len(b) >= _STEM and a[:_STEM] == b[:_STEM]


def _hits(query: list[str], doc: list[str]) -> int:
    return sum(1 for q in query if any(_same(q, d) for d in doc))


def score(query: str, tool: BaseTool) -> int:
    """How well a tool answers a query: words of its purpose are worth most, then its own
    name, then its description. Word matching ignores endings, as Russian and English both
    need; the hand-written words carry the languages apart."""
    asked = words(query)
    if not asked:
        return 0
    alias = words(WORDS.get(tool.name, ""))
    name = words(tool.name.replace("_", " "))
    about = words(tool.description)
    return 3 * _hits(asked, alias) + 2 * _hits(asked, name) + _hits(asked, about)


class ToolSearch(BaseTool):
    """The catalog's index: what is not in the prompt is found here."""

    name: str = "tool_search"
    description: str = (
        "Find a tool by what you want to do, in the person's words. The tools outside the "
        "short list above are not described in this request: ask here first, then call the "
        "tool by the name this returns, with the arguments its schema shows. Returns the "
        "name, one line about it and the full input schema of the best matches."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What the person wants done, in their own words",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5,
                "description": "How many tools to show, three by default",
            },
        },
        "required": ["query"],
    }
    risk: RiskLevel = RiskLevel.SAFE
    #: the tools the prompt does not carry: name -> tool (``Any`` so pydantic keeps them)
    catalog: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def over(cls, tools: Iterable[BaseTool], hidden: Iterable[str]) -> ToolSearch:
        """A search over the tools whose names are in ``hidden``."""
        wanted = set(hidden)
        return cls(catalog={t.name: t for t in tools if t.name in wanted})

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        return CallAssessment(
            risk=RiskLevel.SAFE,
            summary=f"tool_search {str(args.get('query') or '')[:60]}",
        )

    async def execute(self, query: str = "", limit: int = 3, **_: Any) -> ToolResult:
        asked = (query or "").strip()
        if not asked:
            return ToolResult.fail("tool_search needs the query: what to do")
        ranked = sorted(
            ((score(asked, tool), tool) for tool in self.catalog.values()),
            key=lambda pair: (-pair[0], pair[1].name),
        )
        best = [(s, t) for s, t in ranked if s > 0][: max(1, min(int(limit or 3), 5))]
        if not best:
            names = ", ".join(sorted(self.catalog))
            return ToolResult(
                output=f"Nothing matched {asked!r}. The tools there are: {names}. "
                "If none of them does the job, say so instead of inventing one."
            )
        out: list[str] = []
        for _points, tool in best:
            out.append(
                f"- {tool.name}: {tool.description}\n  schema: "
                + json.dumps(tool.parameters, ensure_ascii=False)
            )
        return ToolResult(
            output="\n".join(out)
            + "\n\nCall the tool by name with arguments that fit its schema."
        )
