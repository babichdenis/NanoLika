"""Retail editing, quotation and discounted settlement use the same source."""

from __future__ import annotations

import math
from contextlib import asynccontextmanager

import pytest
from test_cloud import make_stack, sign_up

from nanomuse_cloud import model_pricing
from nanomuse_cloud.config import ModelSpec
from nanomuse_cloud.service import CloudError


@asynccontextmanager
async def opened(client):
    try:
        yield client
    finally:
        await client.aclose()


async def stack():
    models = (
        ModelSpec(
            "text",
            "Text",
            "text",
            sell_in=80,
            sell_out=120,
            context_length=200000,
            max_output=10000,
        ),
        ModelSpec("image", "Picture", "image", kind="image", price_image=1, price_image_2k=2),
        ModelSpec("video", "Clip", "video", kind="video", price_second=1),
        ModelSpec("voice", "Voice", "voice", kind="audio", audio_kind="speech", price_speech=1),
    )
    app, client, sender, up, cloud = make_stack(models=models, rub_per_usd=100, usd_cny=10, topup_fee_pct=0)
    user = await sign_up(client, sender, identifier="pricing@example.test")
    return client, cloud, {"Authorization": "Bearer " + user["api_key"]}


async def test_quote_requires_auth_and_never_spends():
    client, cloud, headers = await stack()
    async with opened(client):
        assert (await client.post("/v1/models/quote", json={"model": "image"})).status_code == 401
        caller = cloud.authenticate(headers["Authorization"][7:])
        before = cloud.db.recent_ledger(caller.account_id)
        response = await client.post("/v1/models/quote", headers=headers, json={"model": "image", "size": "2048x2048"})
        assert response.status_code == 200, response.text
        assert response.json()["total_rub"] == 20
        assert len(cloud.db.recent_ledger(caller.account_id)) == len(before)


async def test_saved_price_visible_in_catalogue_and_actual_image_charge(monkeypatch):
    client, cloud, headers = await stack()
    async with opened(client):
        caller = cloud.authenticate(headers["Authorization"][7:])
        monkeypatch.setattr(cloud, "spend_discount_pct", lambda account: 30)
        route = "/v1/admin/prices/models/image/pricing"
        payload = {"revision": 0, "rates_rub": {"image": 25}}
        assert (await client.put(route, json=payload)).status_code == 401
        saved = await client.put(route, headers={"X-Admin-Token": "admin"}, json=payload)
        assert saved.status_code == 200, saved.text
        assert (await client.put(route, headers={"X-Admin-Token": "admin"}, json=payload)).status_code == 409
        rows = (await client.get("/v1/me/models", headers=headers)).json()["models"]
        image = next(m for m in rows if m["id"] == "image")
        assert image["commerce"]["rates_rub"]["image"] == 25
        assert image["commerce"]["effective_rub"]["image"] == 17.5
        quote = (await client.post("/v1/models/quote", headers=headers, json={"model": "image"})).json()
        cloud.charge_image(caller, cloud.s.model("image"), 1, "test-image")
        ledger = cloud.db._conn.execute("SELECT * FROM ledger WHERE account_id=? ORDER BY id DESC LIMIT 1", (caller.account_id,)).fetchone()
        assert ledger["sale_uy"] == quote["sale_uy"]
        assert ledger["cost_uy"] == 1000000  # wholesale stays separate
        assert model_pricing.policy(cloud, cloud.s.model("image"))["revision"] == 1


async def test_text_quote_matches_charge_and_invalid_quantities_rejected(monkeypatch):
    client, cloud, headers = await stack()
    async with opened(client):
        caller = cloud.authenticate(headers["Authorization"][7:])
        monkeypatch.setattr(cloud, "spend_discount_pct", lambda account: 20)
        quote = model_pricing.amount(cloud, cloud.s.model("text"), {"input_tokens": 1000, "output_tokens": 500}, caller)
        cloud.charge_chat(caller, cloud.s.model("text"), 1000, 500, "test-text")
        assert (
            cloud.db._conn.execute("SELECT * FROM ledger WHERE account_id=? ORDER BY id DESC LIMIT 1", (caller.account_id,)).fetchone()[
                "sale_uy"
            ]
            == quote["sale_uy"]
        )
        for payload in [
            {"input_tokens": -1},
            {"cached_tokens": 2000},
            {"output_tokens": 20000},
            {"input_tokens": True},
        ]:
            response = await client.post("/v1/models/quote", headers=headers, json={"model": "text", **payload})
            assert response.status_code == 400
        with pytest.raises(CloudError):
            model_pricing.save(cloud, cloud.s.model("text"), {"revision": 0, "rates_rub": {"input": math.nan}})


async def test_video_keeps_accepted_price_and_settles_once(monkeypatch):
    client, cloud, headers = await stack()
    async with opened(client):
        caller = cloud.authenticate(headers["Authorization"][7:])
        spec = cloud.s.model("video")
        monkeypatch.setattr(cloud, "spend_discount_pct", lambda account: 30)
        accepted = model_pricing.amount(cloud, spec, {"seconds": 4}, caller)["sale_uy"]
        cloud.db.insert_video_task("fixed-price", caller.account_id, "video", cost_uy=4000000, sale_uy=accepted)
        model_pricing.save(cloud, spec, {"revision": 0, "rates_rub": {"second": 100}})
        monkeypatch.setattr(cloud, "spend_discount_pct", lambda account: 0)
        assert cloud.settle_video_task(cloud.db.video_task("fixed-price"), "SUCCEEDED") is not None
        assert cloud.settle_video_task(cloud.db.video_task("fixed-price"), "SUCCEEDED") is None
        assert (
            cloud.db._conn.execute("SELECT * FROM ledger WHERE account_id=? ORDER BY id DESC LIMIT 1", (caller.account_id,)).fetchone()[
                "sale_uy"
            ]
            == accepted
        )


async def test_favourites_survive_older_shelf_writes():
    client, cloud, headers = await stack()
    async with opened(client):
        first = await client.put("/v1/me/models", headers=headers, json={"favourites": ["image"], "enabled": {}})
        assert first.status_code == 200, first.text
        assert first.json()["shelf"]["favourites"] == ["image"]
        second = await client.put("/v1/me/models", headers=headers, json={"enabled": {}})
        assert second.json()["shelf"]["favourites"] == ["image"]
