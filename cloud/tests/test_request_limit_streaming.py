"""Exercise ASGI messages directly, without TestClient joining streamed chunks."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from tempfile import SpooledTemporaryFile

import pytest


def middleware_module():
    path = Path(__file__).parents[1] / "nanomuse_cloud" / "request_limits.py"
    spec = importlib.util.spec_from_file_location("request_limits_review", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def http_scope(headers=()):
    return {"type": "http", "method": "POST", "path": "/upload", "headers": headers}


@pytest.mark.parametrize("headers", [[], [(b"content-length", b"1")]])
def test_stops_receiving_at_first_over_limit_chunk(headers):
    async def run():
        received = []
        sent = []

        async def receive():
            received.append(True)
            assert len(received) <= 2, "middleware drained the oversized upload"
            return {"type": "http.request", "body": b"x" * 600, "more_body": True}

        async def send(message):
            sent.append(message)

        async def app(*args):
            pytest.fail("parser or endpoint entered before upload was validated")

        await middleware_module().RequestBodyLimitMiddleware(app, 1024)(http_scope(headers), receive, send)
        assert len(received) == 2
        assert sent[0]["status"] == 413

    asyncio.run(run())


def test_declared_oversize_never_reads_input():
    async def run():
        sent = []

        async def receive():
            pytest.fail("declared oversized upload was read")

        async def app(*args):
            pytest.fail("application entered")

        async def send(message):
            sent.append(message)

        await middleware_module().RequestBodyLimitMiddleware(app, 1024)(http_scope([(b"content-length", b"1025")]), receive, send)
        assert sent[0]["status"] == 413

    asyncio.run(run())


def test_disconnect_during_preflight_does_not_enter_parser():
    async def run():
        messages = iter(
            [
                {"type": "http.request", "body": b"partial", "more_body": True},
                {"type": "http.disconnect"},
            ]
        )

        async def receive():
            return next(messages)

        async def unexpected(*args):
            pytest.fail("disconnected request entered app or sent a response")

        await middleware_module().RequestBodyLimitMiddleware(unexpected, 1024)(http_scope(), receive, unexpected)

    asyncio.run(run())


def test_websocket_passes_through_original_callbacks():
    async def run():
        scope = {"type": "websocket"}

        async def receive():
            pytest.fail("middleware consumed websocket message")

        async def send(message):
            pytest.fail("middleware sent websocket message")

        async def app(actual_scope, actual_receive, actual_send):
            assert actual_scope is scope
            assert actual_receive is receive
            assert actual_send is send

        await middleware_module().RequestBodyLimitMiddleware(app, 0)(scope, receive, send)

    asyncio.run(run())


def test_accepted_body_spools_and_replays_bounded_chunks(monkeypatch):
    module = middleware_module()
    files = []

    def spool(*args, **kwargs):
        body = SpooledTemporaryFile(*args, **kwargs)  # noqa: SIM115, middleware owns the context
        files.append(body)
        return body

    monkeypatch.setattr(module, "SpooledTemporaryFile", spool)

    async def run():
        content = b"x" * (1024 * 1024 + 10)
        messages = iter(
            [
                {"type": "http.request", "body": content[:700_000], "more_body": True},
                {"type": "http.request", "body": content[700_000:], "more_body": False},
                {"type": "http.disconnect"},
            ]
        )

        async def receive():
            return next(messages)

        async def app(scope, replay, send):
            assert files[0]._rolled
            chunks = []
            while True:
                message = await replay()
                assert message["type"] == "http.request"
                assert len(message["body"]) <= 64 * 1024
                chunks.append(message["body"])
                if not message["more_body"]:
                    break
            assert b"".join(chunks) == content
            assert await replay() == {"type": "http.disconnect"}

        async def send(message):
            pytest.fail("unexpected response")

        await module.RequestBodyLimitMiddleware(app, len(content))(http_scope(), receive, send)
        assert files[0].closed

    asyncio.run(run())
