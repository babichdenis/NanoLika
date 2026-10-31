"""Модельный журнал: системные изменения и заметки оператора в одном списке."""

from __future__ import annotations

from contextlib import asynccontextmanager

from test_cloud import make_stack

from nanomuse_cloud.config import ModelSpec

ADMIN = {"X-Admin-Token": "admin"}


@asynccontextmanager
async def opened(client):
    try:
        yield client
    finally:
        await client.aclose()


async def stack():
    models = (
        ModelSpec("text", "Text", "text", sell_in=80, sell_out=120, context_length=200000, max_output=10000),
    )
    app, client, sender, up, cloud = make_stack(models=models, rub_per_usd=100, usd_cny=10, topup_fee_pct=0)
    return client, cloud


async def test_note_and_price_change_appear_in_one_timeline():
    client, cloud = await stack()
    async with opened(client):
        note = await client.post(
            "/v1/admin/models/text/notes",
            headers=ADMIN,
            json={"body": "Проверить цену после акции"},
        )
        assert note.status_code == 200, note.text
        assert note.json()["kind"] == "note"

        await client.put(
            "/v1/admin/prices/models/text/draft",
            headers=ADMIN,
            json={"rates_rub": {"output": 150}},
        )
        await client.post("/v1/admin/prices/drafts/publish", headers=ADMIN, json={})

        log = (await client.get("/v1/admin/models/text/log", headers=ADMIN)).json()
        kinds = [event["kind"] for event in log["events"]]
        assert "note" in kinds
        assert any(event["action"] == "model.price" for event in log["events"])
        note_event = next(event for event in log["events"] if event["kind"] == "note")
        assert note_event["body"] == "Проверить цену после акции"


async def test_empty_note_is_refused_and_unknown_model_is_404():
    client, cloud = await stack()
    async with opened(client):
        assert (
            await client.post("/v1/admin/models/text/notes", headers=ADMIN, json={"body": "  "})
        ).status_code == 422
        assert (await client.get("/v1/admin/models/nope/log", headers=ADMIN)).status_code == 404
