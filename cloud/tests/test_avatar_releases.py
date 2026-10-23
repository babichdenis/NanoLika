import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import HTTPException
from test_accounts import make

from nanomuse_cloud.avatar_releases import FILES, AvatarReleases


@pytest.fixture
def store(tmp_path):
    return AvatarReleases(tmp_path / "looks")


def bundle(change=None):
    root = Path(__file__).parents[2] / "assets/brand/avatar"
    files = {name: (root / name).read_bytes() for name in FILES}
    files["manifest.json"] = json.dumps({"label": "Lika original"}).encode()
    if change:
        change(files)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, value in files.items():
            z.writestr(name, value)
    return out.getvalue()


def test_upload_is_immutable_and_does_not_publish(store):
    pack = store.upload(bundle())
    assert store.state()["active"] is None
    assert store.upload(bundle())["id"] == pack["id"]
    result = store.publish(pack["id"], 0)
    assert result["active"] == pack["id"]
    public = store.public()
    assert set(public["stills"]) == {"idle", "working", "waiting", "happy", "error"}
    assert "error" not in public["clips"]
    reset = store.publish(None, result["revision"])
    assert reset["previous"] == pack["id"]
    assert store.publish(reset["previous"], reset["revision"])["active"] == pack["id"]


def test_concurrent_publish_is_rejected(store):
    pack = store.upload(bundle())

    def publish():
        try:
            store.publish(pack["id"], 0)
            return 200
        except HTTPException as e:
            return e.status_code

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(lambda _: publish(), range(2))) == [200, 409]


@pytest.mark.parametrize(
    "change",
    [
        lambda f: f.update({"../outside.webp": b"bad"}),
        lambda f: f.pop("error.webp"),
        lambda f: f.update({"idle.webp": b"<script>bad</script>"}),
        lambda f: f.update({"working.mp4": b"not a video"}),
        lambda f: f.update({"manifest.json": b"not json"}),
    ],
)
def test_invalid_bundle_cannot_publish(store, change):
    with pytest.raises(ValueError):
        store.upload(bundle(change))
    assert store.state()["active"] is None
    assert not list(store.root.glob("*/manifest.json"))


async def test_api_admin_protection_and_cache_headers(tmp_path, monkeypatch):
    monkeypatch.setenv("AVATAR_RELEASES_DIR", str(tmp_path / "api-looks"))
    app, client, *_ = make()
    headers = {"X-Admin-Token": "admin"}
    for path in ["/v1/admin/avatar-releases/upload", "/v1/admin/avatar-releases/publish"]:
        assert (await client.post(path, content=b"anything")).status_code == 401
    assert (await client.get("/v1/admin/avatar-releases")).status_code == 401
    pack = (await client.post("/v1/admin/avatar-releases/upload", content=bundle(), headers=headers)).json()
    assert (await client.get("/v1/brand/avatar")).json()["active"] is None
    r = await client.post("/v1/admin/avatar-releases/publish", json={"release": pack["id"], "expected_revision": 0}, headers=headers)
    assert r.status_code == 200
    r = await client.get("/v1/brand/avatar")
    assert r.headers["cache-control"] == "no-store"
    media = await client.get(r.json()["stills"]["idle"])
    assert media.status_code == 200
    assert "immutable" in media.headers["cache-control"]
    assert (await client.get("/v1/brand/avatar/assets/" + pack["id"] + "/manifest.json")).status_code == 404
    assert (
        await client.post("/v1/admin/avatar-releases/publish", json={"release": pack["id"], "expected_revision": 0}, headers=headers)
    ).status_code == 409
    await client.aclose()
