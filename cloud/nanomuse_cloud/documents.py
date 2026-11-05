"""Word documents from the web chat: `/v1/documents` builds a .docx and returns a link.

The web client has no file tools of its own, and the desktop app is not installed for
every visitor. This door is the same for every account: the model asks for a document,
the relay writes one, and the chat shows a download link. No account files, no computer.
"""

from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from html import escape
from typing import Any

from fastapi import Depends, Request
from fastapi.responses import JSONResponse

from . import s3_files
from .service import Caller, Cloud, CloudError

MAX_TITLE = 200
MAX_TEXT = 300_000

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)


def _run(text: str, *, bold: bool = False, size: int = 24) -> str:
    props = "<w:rPr>"
    if bold:
        props += "<w:b/>"
    props += f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr>'
    return f'<w:r>{props}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _paragraph(text: str, *, bold: bool = False, size: int = 24) -> str:
    return f"<w:p>{_run(text, bold=bold, size=size)}</w:p>"


def _blocks(title: str, text: str) -> list[str]:
    """The title, then a light reading of markdown: headings, bullets, plain lines."""
    out = [_paragraph(title.strip() or "Документ", bold=True, size=36)]
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            out.append("<w:p/>")
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            size = {1: 32, 2: 28, 3: 26}[len(heading.group(1))]
            out.append(_paragraph(heading.group(2).strip(), bold=True, size=size))
            continue
        bullet = re.match(r"^\s*[-*•]\s+(.+)$", line)
        if bullet:
            out.append(_paragraph("• " + bullet.group(1).strip()))
            continue
        number = re.match(r"^\s*(\d+[.)])\s+(.+)$", line)
        if number:
            out.append(_paragraph(number.group(1) + " " + number.group(2).strip()))
            continue
        out.append(_paragraph(line.strip()))
    return out


_XLSX_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
    "</Types>"
)
_XLSX_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
    "</Relationships>"
)
_XLSX_WORKBOOK_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
    "</Relationships>"
)


def _column(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def _rows_from(content: str) -> list[list[str]]:
    text = (content or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            rows: list[list[str]] = []
            for row in parsed:
                if isinstance(row, list):
                    rows.append(["" if cell is None else str(cell) for cell in row])
                else:
                    rows.append([str(row)])
            return rows
    rows = []
    for line in text.splitlines():
        if "\t" in line:
            cells = line.split("\t")
        elif "|" in line:
            cells = line.split("|")
        else:
            cells = [line]
        rows.append([cell.strip() for cell in cells])
    return rows


def make_xlsx(title: str, content: str) -> bytes:
    rows = _rows_from(content)
    sheet_rows = []
    for r, row in enumerate(rows, start=1):
        cells = "".join(
            f'<c r="{_column(c)}{r}" t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'
            for c, value in enumerate(row)
        )
        sheet_rows.append(f'<row r="{r}">{cells}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(sheet_rows)}</sheetData></worksheet>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{escape((title or "Лист1")[:31])}" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _XLSX_CONTENT_TYPES)
        archive.writestr("_rels/.rels", _XLSX_RELS)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", _XLSX_WORKBOOK_RELS)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()


def _safe_member(name: str) -> str:
    clean = re.sub(r"[\\/]+", "/", str(name or "")).strip("/")
    clean = clean.replace("..", "_")
    return clean[:120] or "file.txt"


def make_zip(files: list[dict[str, Any]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in files:
            if not isinstance(item, dict):
                continue
            name = _safe_member(str(item.get("name") or "file.txt"))
            content = str(item.get("content") or "")
            archive.writestr(name, content.encode("utf-8"))
    return buffer.getvalue()


def make_docx(title: str, text: str) -> bytes:
    body = "".join(_blocks(title, text))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}"
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/></w:sectPr>'
        "</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _RELS)
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def _safe_name(title: str, suffix: str) -> str:
    clean = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", title or "").strip()
    clean = re.sub(r"\s+", " ", clean)[:80] or "document"
    return clean + suffix


def install(app: Any, cloud: Cloud, caller_dep: Any) -> None:
    settings = cloud.s

    @app.post("/v1/documents")
    async def create_document(request: Request, caller: Caller = Depends(caller_dep)) -> JSONResponse:
        """A .docx from the text the model wrote, as a link the chat can show."""
        raw = await request.body()
        if len(raw) > 400_000:
            raise CloudError(413, "too_large", "The document text is too large")
        try:
            body = json.loads(raw or b"{}")
        except ValueError as exc:
            raise CloudError(400, "bad_request", "The body is not JSON") from exc
        if not isinstance(body, dict):
            raise CloudError(400, "bad_request", "The body is not an object")
        title = str(body.get("title") or "")[:MAX_TITLE]
        text = str(body.get("content") or "")[:MAX_TEXT]
        kind = str(body.get("format") or "docx").lower()
        if kind not in {"docx", "xlsx", "zip"}:
            raise CloudError(400, "bad_format", "Use docx, xlsx or zip")
        if kind != "zip" and not text.strip():
            raise CloudError(400, "empty_document", "There is nothing to put in the document")
        raw_files = body.get("files")
        files = [row for row in raw_files if isinstance(row, dict)] if isinstance(raw_files, list) else []
        if kind == "zip" and not files:
            raise CloudError(400, "empty_archive", "An archive needs at least one file")
        if not s3_files.enabled(settings):
            raise CloudError(503, "attachments_unconfigured", "File storage is not configured on this relay")
        if kind == "docx":
            data = make_docx(title, text)
            suffix = ".docx"
            mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif kind == "xlsx":
            data = make_xlsx(title, text)
            suffix = ".xlsx"
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            data = make_zip(files)
            suffix = ".zip"
            mime = "application/zip"
        name = _safe_name(title, suffix)
        try:
            key, _url = await s3_files.store(settings, data, mime, name)
        except Exception as exc:  # noqa: BLE001 — the vendor's words stay out of the answer
            logging.getLogger("nanomuse_cloud.documents").warning("documents: S3 refused the file: %s", exc)
            raise CloudError(502, "storage_failed", "The document could not be stored") from exc
        return JSONResponse(
            {
                "name": name,
                "size": len(data),
                "media_url": s3_files.media_url(settings, key),
            },
            headers={"Cache-Control": "private, no-store"},
        )

