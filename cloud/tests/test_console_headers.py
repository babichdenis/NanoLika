"""The console and the operator's page are not framable and run only their own scripts."""

from __future__ import annotations

from test_allowance import make


async def test_app_and_admin_are_not_framed_and_have_a_csp():
    app, client, sender, up, cloud = make()
    try:
        for path, cache in (("/app/", None), ("/app/admin/", "no-store")):
            response = await client.get(path)
            assert response.status_code == 200, response.text
            assert response.headers["x-frame-options"] == "DENY"
            assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
            assert "script-src 'self'" in response.headers["content-security-policy"]
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["referrer-policy"] == "no-referrer"
            if cache is not None:
                assert response.headers["cache-control"] == cache
            await response.aclose()
    finally:
        await client.aclose()
