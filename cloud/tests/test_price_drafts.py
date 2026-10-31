"""Черновик цены: клиенты видят старое, пока оператор не нажмёт «Опубликовать»."""

from __future__ import annotations

from contextlib import asynccontextmanager

from test_cloud import make_stack

from nanomuse_cloud import model_pricing
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
        ModelSpec("image", "Picture", "image", kind="image", price_image=10),
    )
    app, client, sender, up, cloud = make_stack(models=models, rub_per_usd=100, usd_cny=10, topup_fee_pct=0)
    return client, cloud


async def test_draft_is_visible_in_admin_but_not_to_clients():
    client, cloud = await stack()
    async with opened(client):
        response = await client.put(
            "/v1/admin/prices/models/text/draft",
            headers=ADMIN,
            json={"rates_rub": {"input": 100, "output": 150}},
        )
        assert response.status_code == 200, response.text
        assert response.json()["draft"]["changed"] == {"input": 100.0, "output": 150.0}

        # Клиент по-прежнему видит старую цену.
        menu = (await client.get("/v1/menu")).json()["models"]
        text = next(row for row in menu if row["id"] == "text")
        assert text["commerce"]["rates_rub"]["input"] == 80
        assert text["commerce"]["rates_rub"]["output"] == 120

        admin = (await client.get("/v1/admin/prices", headers=ADMIN)).json()
        row = next(row for row in admin["models"] if row["id"] == "text")
        assert admin["price_drafts"]["count"] == 1
        assert row["draft"]["changed"] == {"input": 100.0, "output": 150.0}
        assert row["draft"]["stale"] is False


async def test_publish_writes_price_and_clears_draft():
    client, cloud = await stack()
    async with opened(client):
        await client.put(
            "/v1/admin/prices/models/text/draft",
            headers=ADMIN,
            json={"rates_rub": {"input": 100, "output": 150}},
        )
        published = await client.post("/v1/admin/prices/drafts/publish", headers=ADMIN, json={})
        assert published.status_code == 200, published.text
        assert published.json()["published"] == 1
        assert published.json()["failed"] == []

        policy = model_pricing.policy(cloud, cloud.s.model("text"))
        assert policy["rates_rub"]["input"] == 100
        assert policy["rates_rub"]["output"] == 150
        assert policy["revision"] == 1

        admin = (await client.get("/v1/admin/prices", headers=ADMIN)).json()
        assert admin["price_drafts"]["count"] == 0
        row = next(row for row in admin["models"] if row["id"] == "text")
        assert row["draft"] is None


async def test_stale_draft_does_not_overwrite_a_newer_price():
    client, cloud = await stack()
    async with opened(client):
        await client.put(
            "/v1/admin/prices/models/text/draft",
            headers=ADMIN,
            json={"rates_rub": {"input": 100}},
        )
        # Кто-то успел опубликовать другую цену.
        model_pricing.save(cloud, cloud.s.model("text"), {"revision": 0, "rates_rub": {"input": 90}})

        admin = (await client.get("/v1/admin/prices", headers=ADMIN)).json()
        row = next(row for row in admin["models"] if row["id"] == "text")
        assert row["draft"]["stale"] is True

        published = (await client.post("/v1/admin/prices/drafts/publish", headers=ADMIN, json={})).json()
        assert published["published"] == 0
        assert published["failed"][0]["id"] == "text"
        assert model_pricing.policy(cloud, cloud.s.model("text"))["rates_rub"]["input"] == 90


async def test_bulk_multiplier_stages_instead_of_publishing():
    client, cloud = await stack()
    async with opened(client):
        response = await client.post(
            "/v1/admin/prices/bulk/pricing",
            headers=ADMIN,
            json={"multiplier": 2, "items": [{"id": "text", "input": 30, "output": 40}]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["staged"] == 1
        assert model_pricing.policy(cloud, cloud.s.model("text"))["rates_rub"]["input"] == 80

        admin = (await client.get("/v1/admin/prices", headers=ADMIN)).json()
        row = next(row for row in admin["models"] if row["id"] == "text")
        assert row["draft"]["changed"] == {"input": 60.0, "output": 80.0}


async def test_discard_and_single_drop_remove_drafts():
    client, cloud = await stack()
    async with opened(client):
        for model_id in ("text", "image"):
            response = await client.put(
                f"/v1/admin/prices/models/{model_id}/draft",
                headers=ADMIN,
                json={"rates_rub": {"input": 100} if model_id == "text" else {"image": 25}},
            )
            assert response.status_code == 200, response.text

        assert (await client.delete("/v1/admin/prices/models/text/draft", headers=ADMIN)).json()["removed"] is True
        admin = (await client.get("/v1/admin/prices", headers=ADMIN)).json()
        assert admin["price_drafts"]["count"] == 1

        discarded = await client.post("/v1/admin/prices/drafts/discard", headers=ADMIN, json={})
        assert discarded.json()["removed"] == 1
        assert model_pricing.policy(cloud, cloud.s.model("image"))["rates_rub"]["image"] == 100
