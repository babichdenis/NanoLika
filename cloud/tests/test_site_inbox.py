"""Входящие с сайта: форма поддержки и чат попадают в базу, админка их видит.

Проверяем три вещи: сообщение посетителя сохраняется с темой и адресом, копия
чата приходит с вопросом и ответом, а читать всё это можно только с админским
паролем — иначе страница не должна отдавать ни строчки.
"""

from __future__ import annotations

from test_bot import client_of, stack

ADMIN = {"X-Admin-Token": "admin"}


async def test_a_support_message_is_kept_with_its_topic_and_address():
    app, cloud = stack()
    async with client_of(app) as client:
        sent = await client.post(
            "/v1/site/feedback",
            json={
                "topic": "Оплата или пополнение",
                "email": "guest@example.test",
                "message": "Не пришло пополнение на 300 рублей.",
                "page": "/support.html",
            },
            headers={"X-Forwarded-For": "10.0.0.5, 172.18.0.1", "User-Agent": "TestBrowser/1.0"},
        )
        assert sent.status_code == 200
        assert sent.json() == {"ok": True}

        rows = cloud.db.site_messages(source="feedback")
        assert len(rows) == 1
        row = rows[0]
        assert row["topic"] == "Оплата или пополнение"
        assert row["email"] == "guest@example.test"
        assert row["body"] == "Не пришло пополнение на 300 рублей."
        assert row["page"] == "/support.html"
        # за Caddy адрес посетителя — первый в X-Forwarded-For, а не адрес прокси
        assert row["ip"] == "10.0.0.5"
        assert row["read_at"] is None


async def test_a_chat_copy_keeps_the_question_the_answer_and_its_own_clock():
    app, cloud = stack()
    async with client_of(app) as client:
        sent = await client.post(
            "/v1/site/chat",
            json={
                "question": "Чем поможет Лика?",
                "reply": "Разберёт почту и файлы.",
                "page": "/",
                "session": "s-42",
                "code": "ok",
                "at": 1_700_000_000,
            },
        )
        assert sent.status_code == 200
        row = cloud.db.site_messages(source="chat")[0]
        # время посетителя, а не время, когда копия дошла до релея
        assert row["at"] == 1_700_000_000
        assert row["body"] == "Чем поможет Лика?"
        assert row["reply"] == "Разберёт почту и файлы."
        assert row["session"] == "s-42"
        assert row["code"] == "ok"


async def test_the_inbox_is_closed_without_the_admin_password():
    app, cloud = stack()
    cloud.db.add_site_message("feedback", "Тайна", email="x@example.test")
    async with client_of(app) as client:
        for path in ("/v1/admin/inbox",):
            assert (await client.get(path)).status_code == 401
            assert (await client.get(path, headers={"X-Admin-Token": "wrong"})).status_code == 401
        marked = await client.post("/v1/admin/inbox/read", json={})
        assert marked.status_code == 401


async def test_an_operator_reads_everything_new_first_and_marks_it_read():
    app, cloud = stack()
    cloud.db.add_site_message("chat", "Первый вопрос", reply="Ответ", code="ok")
    cloud.db.add_site_message("feedback", "Второе письмо", topic="Идея или предложение", email="a@example.test")

    async with client_of(app) as client:
        body = (await client.get("/v1/admin/inbox", headers=ADMIN)).json()
        assert body["counts"]["chat"]["total"] == 1
        assert body["counts"]["feedback"]["total"] == 1
        assert body["counts"]["feedback"]["unread"] == 1
        # свежее сверху
        assert [m["body"] for m in body["messages"]] == ["Второе письмо", "Первый вопрос"]

        one = (await client.get("/v1/admin/inbox?source=chat", headers=ADMIN)).json()
        assert [m["body"] for m in one["messages"]] == ["Первый вопрос"]

        searched = (await client.get("/v1/admin/inbox?q=Второе", headers=ADMIN)).json()
        assert [m["body"] for m in searched["messages"]] == ["Второе письмо"]

        marked = (await client.post("/v1/admin/inbox/read", json={}, headers=ADMIN)).json()
        assert marked["marked"] == 2
        after = (await client.get("/v1/admin/inbox", headers=ADMIN)).json()
        assert all(m["read_at"] for m in after["messages"])
        assert after["counts"]["feedback"]["unread"] == 0


async def test_read_marks_can_name_one_message():
    app, cloud = stack()
    first = cloud.db.add_site_message("chat", "Первый", code="ok")
    cloud.db.add_site_message("chat", "Второй", code="ok")
    async with client_of(app) as client:
        marked = (await client.post("/v1/admin/inbox/read", json={"ids": [first]}, headers=ADMIN)).json()
        assert marked["marked"] == 1
        rows = {row["body"]: row for row in cloud.db.site_messages()}
        assert rows["Первый"]["read_at"] is not None
        assert rows["Второй"]["read_at"] is None


async def test_an_empty_message_is_refused_and_a_flood_is_limited():
    app, cloud = stack()
    async with client_of(app) as client:
        empty = await client.post("/v1/site/feedback", json={"message": "   ", "email": "a@example.test"})
        assert empty.status_code == 400
        assert cloud.db.site_messages() == []

        # форма и чат считаются вместе: один адрес не забивает ленту
        for _ in range(10):
            ok = await client.post(
                "/v1/site/feedback",
                json={"message": "Сообщение", "email": "a@example.test"},
                headers={"X-Forwarded-For": "10.0.0.9"},
            )
            assert ok.status_code == 200
        refused = await client.post(
            "/v1/site/chat",
            json={"question": "Ещё вопрос", "reply": "Ответ"},
            headers={"X-Forwarded-For": "10.0.0.9"},
        )
        assert refused.status_code == 429
        assert len(cloud.db.site_messages()) == 10
