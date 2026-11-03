"""A clip costs real money: the tool must say what it took, keep the file when only the
link fails, and leave the refusal's code in the text."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from nanomuse.cloud import CloudError
from nanomuse.tools.video import Video


class FakeCloud:
    def __init__(self, fail: str = "", upload_fails: bool = False) -> None:
        self.fail = fail
        self.upload_fails = upload_fails
        self.made: list[dict[str, Any]] = []
        self.uploaded: list[dict[str, Any]] = []

    async def me_models(self) -> dict[str, Any]:
        return {"shelf": {"default": {"video": "wan-2.5"}}}

    async def models(self) -> list[dict[str, Any]]:
        return [{"id": "wan-2.5", "kind": "video"}]

    async def video(self, model, prompt, *, seconds=5, size="", resolution=""):
        if self.fail:
            raise CloudError(402, self.fail, "This clip costs ₽12.40; the balance has ₽3.00.")
        self.made.append({"model": model, "prompt": prompt, "seconds": seconds, "size": size})
        return b"\x00\x00\x00\x18ftypmp42", {"charged": 900, "charged_rub": 0.71}

    async def upload(self, name, data, mime):
        if self.upload_fails:
            raise CloudError(502, "upstream", "the storage did not answer")
        self.uploaded.append({"name": name, "mime": mime, "bytes": len(data)})
        return f"https://cloud.example.test/v1/files/x/{name}"


def tool(tmp_path: Path, cloud: FakeCloud) -> Video:
    return Video(workspace=tmp_path, extra_roots=[], cloud=lambda: cloud)


def test_a_clip_says_what_it_cost_in_rubles(tmp_path: Path):
    cloud = FakeCloud()
    result = asyncio.run(tool(tmp_path, cloud).execute(prompt="кот на скейте"))
    assert result.ok, result.error
    assert "videos/" in result.output and "link https://" in result.output
    assert "charged ₽0.71" in result.output
    assert cloud.made[0]["model"] == "wan-2.5" and cloud.made[0]["seconds"] == 5


def test_a_failed_upload_keeps_the_paid_clip(tmp_path: Path):
    cloud = FakeCloud(upload_fails=True)
    result = asyncio.run(tool(tmp_path, cloud).execute(prompt="дом"))
    assert result.ok, result.error
    assert "no link" in result.output
    files = list((tmp_path / "videos").glob("*.mp4"))
    assert len(files) == 1 and files[0].read_bytes().startswith(b"\x00\x00\x00\x18ftyp")


def test_a_short_balance_keeps_its_code(tmp_path: Path):
    result = asyncio.run(tool(tmp_path, FakeCloud(fail="insufficient_funds")).execute(prompt="кот"))
    assert not result.ok
    assert "balance" in result.error.lower() and "insufficient_funds" in result.error
