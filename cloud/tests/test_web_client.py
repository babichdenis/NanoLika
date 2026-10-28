from unittest.mock import Mock

import pytest
from test_accounts import auth, make
from test_cloud import sign_up


@pytest.mark.parametrize("stream", [False, True])
async def test_private_browser_request_does_not_collect_samples(stream):
    app, client, sender, upstream, cloud, settings = make()
    login = await sign_up(client, sender, "web-private@example.com")
    headers = auth(login["api_key"])
    await client.post("/v1/me/contribute", json={"on": True}, headers=headers)
    cloud.keep_sample = Mock()
    body = {"model": "qwen3.8-27b", "messages": [{"role": "user", "content": "hello"}], "stream": stream, "store": False}
    response = await client.post("/v1/chat/completions", json=body, headers=headers)
    assert response.status_code == 200, response.text
    cloud.keep_sample.assert_not_called()
    body["store"] = True
    response = await client.post("/v1/chat/completions", json=body, headers=headers)
    assert response.status_code == 200, response.text
    cloud.keep_sample.assert_called_once()
    await client.aclose()


async def test_web_shell_is_public_but_chat_requires_authentication():
    app, client, *_ = make()
    response = await client.get("/web/")
    assert response.status_code == 200
    assert "nanoLika" in response.text
    assert "script-src 'self'" in response.headers["content-security-policy"]
    for name in ["manifest.webmanifest", "sw.js", "app.js", "assets/icon-192.png"]:
        assert (await client.get("/web/" + name)).status_code == 200
    assert (await client.post("/v1/chat/completions", json={"messages": []})).status_code == 401
    assert (await client.get("/web/../api.py")).status_code == 404
    await client.aclose()
