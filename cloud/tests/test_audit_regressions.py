"""Interrupted provider streams, atomic billing and admission before a provider answers."""

from __future__ import annotations

import json
import sqlite3

import httpx
import pytest
from starlette.requests import Request

from nanomuse_cloud.api import create_app
from nanomuse_cloud.config import ModelSpec, Settings
from nanomuse_cloud.db import Database
from nanomuse_cloud.service import Caller, Cloud, CloudError, Usage, estimate_tokens, usage_from_json


@pytest.fixture
def isolated_cloud():
    models = (
        ModelSpec(id="chat", name="Chat", upstream="chat", price_in=1, price_out=4, recommended=True),
        ModelSpec(id="video", name="Video", upstream="video", kind="video", per_clip=100),
    )
    settings = Settings(
        database=":memory:",
        secret="test-only-secret",
        upstream_key="test-key",
        models=models,
        signup_tokens=0,
        allowance_cny=0,
        per_minute_requests=1,
        daily_cap_tokens=0,
    )
    db = Database(":memory:")
    db.create_account("test-hash", "email", "test", 0, account_id="test-account")
    cloud = Cloud(settings, db)
    caller = Caller(
        key_hash="test-key-hash",
        account_id="test-account",
        channel="email",
        hint="test",
        granted=0,
        used=0,
        account_created_at=0,
        member=True,
    )
    yield cloud, caller
    db.close()


def test_video_failure_rolls_back_the_paid_flag_and_retry_charges_once(isolated_cloud):
    cloud, _ = isolated_cloud
    db = cloud.db
    db.insert_video_task("task", "test-account", "video", cost_uy=500)
    db._conn.execute("CREATE TRIGGER fail_ledger BEFORE INSERT ON ledger BEGIN SELECT RAISE(ABORT, 'simulated full ledger'); END")
    with pytest.raises(sqlite3.IntegrityError):
        cloud.settle_video_task(db.video_task("task"), "SUCCEEDED")
    assert db.video_task("task")["charged"] == 0
    assert db.account("test-account")["used"] == 0
    db._conn.execute("DROP TRIGGER fail_ledger")
    assert cloud.settle_video_task(db.video_task("task"), "SUCCEEDED") == 100
    assert cloud.settle_video_task(db.video_task("task"), "SUCCEEDED") is None
    assert db.requests_since("test-account", 0) == 1
    assert db.account("test-account")["used"] == 100


def test_failed_provider_attempt_still_consumes_rate_limit(isolated_cloud):
    cloud, caller = isolated_cloud
    cloud.check_budget(caller, request_id="failed")
    cloud.settle(caller, "failed")  # no ledger: upstream refused
    with pytest.raises(CloudError) as refused:
        cloud.check_budget(caller, request_id="retry")
    assert refused.value.code == "rate_limited"
    assert cloud.db.requests_since(caller.account_id, 0) == 0


@pytest.mark.parametrize("completion,reasoning", [(50, 20), (6, 20)])
def test_reasoning_uses_the_provider_shape_not_relative_size(completion, reasoning):
    native = {"prompt_tokens": 10, "completion_tokens": completion, "output_tokens_details": {"reasoning_tokens": reasoning}}
    assert usage_from_json({"usage": native}) == Usage(10, completion + reasoning)
    inclusive = {
        "prompt_tokens": 10,
        "completion_tokens": completion + reasoning,
        "completion_tokens_details": {"reasoning_tokens": reasoning},
    }
    assert usage_from_json({"usage": inclusive}) == Usage(10, completion + reasoning)
    responses = {"input_tokens": 10, "output_tokens": completion + reasoning, "output_tokens_details": {"reasoning_tokens": reasoning}}
    assert usage_from_json({"usage": responses}) == Usage(10, completion + reasoning)


class BrokenStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield (
            "data: "
            + json.dumps({"choices": [{"delta": {"tool_calls": [{"function": {"name": "write_file", "arguments": "x" * 3000}}]}}]})
            + "\n\n"
        ).encode()
        raise httpx.ReadError("simulated disconnection")


async def stream_response(cloud, caller):
    app = create_app(cloud.s, cloud, upstream_transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=BrokenStream())))
    endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/v1/chat/completions")

    async def receive():
        return {
            "type": "http.request",
            "body": json.dumps({"model": "chat", "stream": True, "messages": [{"role": "user", "content": "write a file"}]}).encode(),
            "more_body": False,
        }

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat/completions",
            "headers": [],
            "query_string": b"",
            "server": ("cloud.test", 443),
            "scheme": "https",
        },
        receive,
    )
    return app, await endpoint(request, caller)


async def test_broken_tool_stream_is_metered_and_reservation_released(isolated_cloud):
    cloud, caller = isolated_cloud
    app, response = await stream_response(cloud, caller)
    try:
        chunks = [chunk async for chunk in response.body_iterator]
        assert b"tool_calls" in b"".join(chunks)
        rows = cloud.db.usage_by_kind(0, caller.account_id)
        assert len(rows) == 1 and rows[0]["completion_tokens"] >= estimate_tokens("x" * 3000)
        assert cloud.in_flight.count(caller.account_id) == 0
    finally:
        await app.state.http.aclose()


async def test_ledger_error_always_releases_the_stream_reservation(isolated_cloud, monkeypatch):
    cloud, caller = isolated_cloud

    def fail(*args, **kwargs):
        raise RuntimeError("simulated ledger error")

    monkeypatch.setattr(cloud, "charge_chat", fail)
    app, response = await stream_response(cloud, caller)
    try:
        with pytest.raises(RuntimeError, match="ledger error"):
            async for _ in response.body_iterator:
                pass
        assert cloud.in_flight.count(caller.account_id) == 0
    finally:
        await app.state.http.aclose()


def test_removed_video_model_still_uses_the_price_saved_at_submission(isolated_cloud, monkeypatch):
    cloud, _ = isolated_cloud
    monkeypatch.setattr(type(cloud.s), "unlisted_model", lambda *_args: None)
    cloud.db.insert_video_task("retired-task", "test-account", "retired", cost_uy=500)
    assert cloud.settle_video_task(cloud.db.video_task("retired-task"), "SUCCEEDED") == 0
    assert cloud.db.video_task("retired-task")["charged"] == 1
    rows = cloud.db.usage_by_kind(0, "test-account")
    assert len(rows) == 1 and rows[0]["cost_uy"] == 500
