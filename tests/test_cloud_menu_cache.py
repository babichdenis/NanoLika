"""The relay's menu is read once in a while, not before every picture and every phrase."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from nanomuse import cloud as cloud_mod
from nanomuse.cloud import CloudClient


def relay(menu: dict, calls: list[str]) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json=menu)

    return httpx.MockTransport(handler)


def client_with(menu: dict, calls: list[str]) -> CloudClient:
    client = CloudClient("https://relay.test", "k")
    asyncio.run(client._client.aclose())
    client._client = httpx.AsyncClient(transport=relay(menu, calls), base_url="https://relay.test")
    return client


MENU = {"data": [{"id": "flux", "kind": "image"}]}
SHELF = {"models": [{"id": "flux", "kind": "image"}], "shelf": {"default": {"image": "flux"}}}


def test_two_calls_read_the_menu_once():
    calls: list[str] = []
    client = client_with(MENU, calls)
    first = asyncio.run(client.models())
    second = asyncio.run(client.models())
    assert first == second and calls == ["/v1/models"]


def test_a_short_ttl_asks_again(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(cloud_mod, "MODELS_TTL_S", 0.0)
    client = client_with(MENU, calls)
    asyncio.run(client.models())
    asyncio.run(client.models())
    assert calls == ["/v1/models", "/v1/models"]


def test_a_model_that_left_the_menu_is_read_fresh(monkeypatch):
    calls: list[str] = []
    client = client_with(MENU, calls)
    asyncio.run(client.models())
    client.forget_models()
    asyncio.run(client.models())
    assert calls == ["/v1/models", "/v1/models"]


def test_the_shelf_is_kept_with_the_menu():
    calls: list[str] = []
    client = client_with(SHELF, calls)
    assert asyncio.run(client.me_models())["shelf"]["default"]["image"] == "flux"
    asyncio.run(client.me_models())
    assert calls == ["/v1/me/models"]
