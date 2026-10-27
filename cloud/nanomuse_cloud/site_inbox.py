"""Входящие с сайта: чат на первой странице и форма поддержки.

Два источника, одна страница в админке (`/app/admin/inbox/`):

* **чат** — виджет на главной спрашивает Лику о продукте. Сервис чата отвечает
  посетителю сам, а копию пары «вопрос — ответ» присылает сюда, чтобы её было
  видно оператору. Без этого копия жила только в `chat.jsonl` на его машине;
* **форма** — страница поддержки. Раньше она открывала почтовую программу
  посетителя и ничего не сохраняла: тема, адрес и текст существовали только в
  письме. Теперь то же самое записывается сюда, а почта остаётся запасным путём,
  если браузер не смог отправить.

Маршруты:

    POST /v1/site/feedback        — форма поддержки (публичный, с лимитом)
    POST /v1/site/chat            — копия сообщения чата (публичный, с лимитом)
    GET  /v1/admin/inbox          — что пришло, для админки (админский токен)
    POST /v1/admin/inbox/read     — отметить прочитанным (админский токен)

Публичные маршруты пишут только текст, который прислали, и не отвечают ничем
внутренним: ни числом записей, ни идентификатором, только `{"ok": true}`.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# Сколько сообщений с одного адреса за окно принимаем. Форма и чат считаются
# вместе: один посетитель не должен забить ленту, а десять сообщений в час —
# это ещё человек, а не скрипт.
BURST = 10
WINDOW_S = 3600

# Пределы длины: в базу не попадает ничего бесконечного.
MAX_BODY = 4000
MAX_REPLY = 4000
MAX_EMAIL = 254
MAX_TOPIC = 80
MAX_PAGE = 200


class FeedbackBody(BaseModel):
    """Что присылает форма поддержки."""

    topic: str = Field(default="", max_length=MAX_TOPIC)
    email: str = Field(default="", max_length=MAX_EMAIL)
    message: str = Field(min_length=1, max_length=MAX_BODY)
    page: str = Field(default="", max_length=MAX_PAGE)


class ChatBody(BaseModel):
    """Что присылает сервис чата после каждого ответа."""

    question: str = Field(default="", max_length=MAX_BODY)
    reply: str = Field(default="", max_length=MAX_REPLY)
    page: str = Field(default="", max_length=MAX_PAGE)
    session: str = Field(default="", max_length=80)
    code: str = Field(default="", max_length=40)
    at: int | None = None


def client_ip(request: Request) -> str:
    """Адрес посетителя: за Caddy он приходит в X-Forwarded-For."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:60]
    return (request.client.host if request.client else "")[:60]


class Limiter:
    """Окно на адрес: сколько сообщений уже было за последний час."""

    def __init__(self, burst: int = BURST, window: int = WINDOW_S) -> None:
        self.burst = burst
        self.window = window
        self.seen: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, who: str) -> bool:
        moment = time.time()
        window = self.seen[who]
        while window and moment - window[0] > self.window:
            window.popleft()
        if len(window) >= self.burst:
            return False
        window.append(moment)
        return True


def _clean(text: str, limit: int) -> str:
    """Текст как есть, но без управляющих символов и в пределах длины."""
    kept = "".join(ch for ch in (text or "") if ch == "\n" or ch == "\t" or ch >= " ")
    return kept.strip()[:limit]


def install(app: Any, cloud: Any, admin_dep: Any) -> None:
    """Включает приём с сайта и выдачу в админку."""
    limiter = Limiter()

    @app.post("/v1/site/feedback")
    async def site_feedback(body: FeedbackBody, request: Request) -> Any:
        who = client_ip(request)
        if not limiter.allow(f"any:{who}"):
            return JSONResponse({"ok": False, "error": "too_many"}, status_code=429)
        message = _clean(body.message, MAX_BODY)
        if not message:
            return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
        cloud.db.add_site_message(
            "feedback",
            message,
            ip=who,
            ua=(request.headers.get("user-agent") or "")[:300],
            email=_clean(body.email, MAX_EMAIL),
            topic=_clean(body.topic, MAX_TOPIC),
            page=_clean(body.page, MAX_PAGE),
        )
        return {"ok": True}

    @app.post("/v1/site/chat")
    async def site_chat(body: ChatBody, request: Request) -> Any:
        who = client_ip(request)
        if not limiter.allow(f"any:{who}"):
            return JSONResponse({"ok": False, "error": "too_many"}, status_code=429)
        question = _clean(body.question, MAX_BODY)
        if not question:
            return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
        cloud.db.add_site_message(
            "chat",
            question,
            at=int(body.at) if body.at else None,
            ip=who,
            ua=(request.headers.get("user-agent") or "")[:300],
            reply=_clean(body.reply, MAX_REPLY),
            page=_clean(body.page, MAX_PAGE),
            session=_clean(body.session, 80),
            code=_clean(body.code, 40),
        )
        return {"ok": True}

    @app.get("/v1/admin/inbox", dependencies=[Depends(admin_dep)])
    async def admin_inbox(source: str = "", q: str = "", limit: int = 100, before: int | None = None) -> dict:
        rows = cloud.db.site_messages(source=source, search=q, limit=limit, before=before)
        return {
            "counts": cloud.db.site_message_counts(),
            "messages": [dict(row) for row in rows],
            "limit": max(1, min(limit, 500)),
        }

    @app.post("/v1/admin/inbox/read", dependencies=[Depends(admin_dep)])
    async def admin_inbox_read(body: dict | None = None) -> dict:
        ids = (body or {}).get("ids")
        wanted = [int(i) for i in ids if str(i).isdigit()] if isinstance(ids, list) else None
        marked = cloud.db.mark_site_messages_read(wanted)
        return {"ok": True, "marked": marked}
