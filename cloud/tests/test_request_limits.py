"""Oversized multipart requests stop before FastAPI parses fields or runs endpoints."""

from __future__ import annotations

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request as StarletteRequest

from nanomuse_cloud.request_limits import RequestBodyLimitMiddleware


def world(limit=1024):
    app = FastAPI()
    entered = []

    @app.post("/upload")
    async def upload(file: UploadFile = File(...)):
        entered.append(True)
        return {"size": len(await file.read())}

    @app.post("/raw")
    async def raw(request: Request):
        entered.append(True)
        return {"size": len(await request.body())}

    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=limit)
    app.add_middleware(CORSMiddleware, allow_origins=["https://nanolika.ru"])
    return TestClient(app), entered


def test_large_declared_multipart_stops_before_form_parser(monkeypatch):
    client, entered = world()
    parsed = []
    original = StarletteRequest._get_form

    async def form(self, **kwargs):
        parsed.append(True)
        return await original(self, **kwargs)

    monkeypatch.setattr(StarletteRequest, "_get_form", form)
    with client:
        response = client.post("/upload", files={"file": ("a.bin", b"x" * 5000)}, headers={"Origin": "https://nanolika.ru"})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "too_large"
    assert response.headers["access-control-allow-origin"] == "https://nanolika.ru"
    assert parsed == entered == []


def test_chunked_multipart_stops_without_content_length():
    client, entered = world()
    produced = []

    def chunks():
        for _ in range(20):
            produced.append(True)
            yield b"x" * 300

    with client:
        response = client.post("/upload", content=chunks(), headers={"Content-Type": "multipart/form-data; boundary=part"})
    assert response.status_code == 413
    assert entered == []


def test_small_declared_length_does_not_bypass_stream_limit():
    client, entered = world()
    with client:
        response = client.post("/raw", content=iter([b"x" * 700, b"y" * 700]), headers={"Content-Length": "10"})
    assert response.status_code == 413
    assert entered == []


def test_exact_boundary_and_valid_multipart_are_replayed_intact():
    client, entered = world()
    with client:
        assert client.post("/raw", content=b"x" * 1024).json() == {"size": 1024}
        assert client.post("/upload", files={"file": ("a.bin", b"abc")}).json() == {"size": 3}
    assert len(entered) == 2


def test_valid_large_upload_survives_disk_spooling():
    client, entered = world(2 * 1024 * 1024)
    with client:
        response = client.post("/upload", files={"file": ("a.bin", b"x" * (1024 * 1024 + 10))})
    assert response.status_code == 200
    assert response.json()["size"] == 1024 * 1024 + 10
    assert entered == [True]


def test_real_relay_rejects_multipart_before_parser_and_endpoint(monkeypatch):
    import asyncio

    from nanomuse_cloud.api import create_app
    from nanomuse_cloud.config import Settings

    app = create_app(
        Settings(
            database=":memory:",
            secret="request-limit-test",
            max_request_bytes=1024,
            geoip_enabled=False,
            github_collect=False,
        )
    )
    parsed = []
    entered = []

    async def unexpected_parser(self, **kwargs):
        parsed.append(True)
        raise AssertionError("oversized body reached multipart parser")

    async def unexpected_endpoint(*args, **kwargs):
        entered.append(True)
        raise AssertionError("oversized body reached upload endpoint")

    monkeypatch.setattr(StarletteRequest, "_get_form", unexpected_parser)
    route = next(route for route in app.routes if route.path == "/api/v1/uploads")
    monkeypatch.setattr(route.dependant, "call", unexpected_endpoint)
    try:
        # No lifespan: this checks real routing/middleware without background network jobs.
        response = TestClient(app).post(
            "/api/v1/uploads",
            files={"file": ("frame.png", b"x" * 5000, "image/png")},
            headers={"Origin": "https://nanolika.ru"},
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "too_large"
        assert response.json()["error"]["max_bytes"] == 1024
        assert response.headers["access-control-allow-origin"] == "https://nanolika.ru"
        assert parsed == entered == []
    finally:
        asyncio.run(app.state.http.aclose())
        app.state.cloud.db.close()
