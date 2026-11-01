"""The picture tool: a chat can draw, not only describe."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from nanomuse.cloud import CloudError
from nanomuse.tools.picture import Picture

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()


class FakeCloud:
    def __init__(self, *, default: str = "nano-banana", fail: str = "") -> None:
        self.drawn: list[dict] = []
        self.default = default
        self.fail = fail

    async def me_models(self):
        shelf = {"default": {"image": self.default} if self.default else {}, "favourites": []}
        return {"models": [], "shelf": shelf}

    async def models(self):
        return [
            {"id": "text", "nanomuse": {"kind": "chat"}},
            {"id": "flux-lite", "nanomuse": {"kind": "image"}},
        ]

    async def image(self, model, prompt, *, size=""):
        if self.fail:
            raise CloudError(402, self.fail, "not enough")
        self.drawn.append({"model": model, "prompt": prompt, "size": size})
        return b"\x89PNG\r\n\x1a\nfake", {"charged": 120}


def tool(tmp_path: Path, cloud: FakeCloud | None = None) -> Picture:
    return Picture(workspace=tmp_path, extra_roots=[], cloud=lambda: cloud or FakeCloud())


def test_a_picture_is_drawn_with_the_shelf_model_and_saved(tmp_path: Path):
    cloud = FakeCloud()
    result = asyncio.run(tool(tmp_path, cloud).execute(prompt="рыжий кот", size="1024x1024"))
    assert result.ok, result.error
    assert "pictures/" in result.output and "charged 120" in result.output
    assert cloud.drawn == [{"model": "nano-banana", "prompt": "рыжий кот", "size": "1024x1024"}]
    files = list((tmp_path / "pictures").glob("*.png"))
    assert len(files) == 1 and files[0].read_bytes().startswith(b"\x89PNG")


def test_the_first_picture_model_is_used_when_the_shelf_has_none(tmp_path: Path):
    cloud = FakeCloud(default="")
    result = asyncio.run(tool(tmp_path, cloud).execute(prompt="дом"))
    assert result.ok, result.error
    assert cloud.drawn[0]["model"] == "flux-lite"


def test_a_named_model_wins(tmp_path: Path):
    cloud = FakeCloud()
    result = asyncio.run(tool(tmp_path, cloud).execute(prompt="дом", model="flux-pro"))
    assert result.ok, result.error
    assert cloud.drawn[0]["model"] == "flux-pro"


def test_the_prompt_is_required_and_the_size_is_checked(tmp_path: Path):
    assert not asyncio.run(tool(tmp_path).execute(prompt="  ")).ok
    assert "size" in asyncio.run(tool(tmp_path).execute(prompt="кот", size="big")).error


def test_a_refusal_is_said_not_swallowed(tmp_path: Path):
    result = asyncio.run(tool(tmp_path, FakeCloud(fail="insufficient_funds")).execute(prompt="кот"))
    assert not result.ok and "insufficient_funds" in result.error
