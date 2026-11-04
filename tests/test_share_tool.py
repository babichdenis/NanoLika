"""`share_file`: a document gets a link the other devices can open."""

from __future__ import annotations

import asyncio
from pathlib import Path

from nanomuse.tools.share import ShareFile

LINK = "https://cloud.nanolika.ru/v1/files/abc/letter.docx"


class FakeCloud:
    def __init__(self, link: str = LINK) -> None:
        self.link = link
        self.uploaded: list[tuple[str, bytes, str]] = []

    async def upload(self, name: str, data: bytes, mime: str) -> str:
        self.uploaded.append((name, data, mime))
        return self.link


def test_a_document_gets_a_link(tmp_path: Path):
    (tmp_path / "documents").mkdir()
    (tmp_path / "documents" / "letter.docx").write_bytes(b"docx-bytes")
    cloud = FakeCloud()
    tool = ShareFile(workspace=tmp_path, cloud=lambda: cloud)
    result = asyncio.run(tool.execute(path="documents/letter.docx"))
    assert not result.error
    assert cloud.uploaded and cloud.uploaded[0][0] == "letter.docx"
    assert cloud.uploaded[0][1] == b"docx-bytes"
    assert "wordprocessingml" in cloud.uploaded[0][2]
    assert LINK in result.output
    assert "[letter.docx](" in result.output


def test_a_missing_file_is_refused(tmp_path: Path):
    tool = ShareFile(workspace=tmp_path, cloud=lambda: FakeCloud())
    result = asyncio.run(tool.execute(path="documents/none.docx"))
    assert result.error


def test_no_storage_is_said_plainly(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x", "utf-8")
    tool = ShareFile(workspace=tmp_path, cloud=None)
    result = asyncio.run(tool.execute(path="a.txt"))
    assert result.error and "storage" in result.error
