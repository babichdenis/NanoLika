"""A MAX message with an unknown outcome must not be sent again as a fallback."""

from __future__ import annotations

import json

import httpx
import pytest

from nanomuse.channels.max import MaxChannel, MaxError


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", 401, 429, 503, "body_error"])
async def test_uncertain_or_non_format_failure_does_not_send_again(failure):
    received = []

    async def handler(request):
        received.append(json.loads(request.content))
        if failure == "timeout":
            # The remote server may have accepted it before its answer was lost.
            raise httpx.ReadTimeout("response lost", request=request)
        if failure == "body_error":
            return httpx.Response(200, json={"success": False, "code": "fixture.error"})
        return httpx.Response(failure, json={"code": "fixture.error"})

    channel = MaxChannel({"bot_token": "fixture", "webhook_secret": "fixture"})
    channel._http = httpx.AsyncClient(base_url="https://example.test", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(MaxError):
            await channel.send("123", "**hello**")
        assert len(received) == 1
        assert received[0]["format"] == "html"
    finally:
        await channel._http.aclose()


@pytest.mark.asyncio
async def test_explicit_http_400_allows_one_plain_text_fallback():
    received = []

    async def handler(request):
        received.append(json.loads(request.content))
        if len(received) == 1:
            return httpx.Response(400, json={"code": "fixture.bad.request"})
        return httpx.Response(200, json={"message": {"body": {"mid": "provider-id"}}})

    channel = MaxChannel({"bot_token": "fixture", "webhook_secret": "fixture"})
    channel._http = httpx.AsyncClient(base_url="https://example.test", transport=httpx.MockTransport(handler))
    try:
        assert await channel.send("123", "**hello**") == "provider-id"
        assert received == [{"text": "<b>hello</b>", "format": "html"}, {"text": "hello"}]
    finally:
        await channel._http.aclose()
