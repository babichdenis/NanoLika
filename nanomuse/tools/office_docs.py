"""Bounded, structured DOCX/XLSX editing for the shared cloud bot.

The model supplies data and explicit operations, never Python code. This is deliberately
not a replacement for the isolated executor needed by arbitrary document workflows.
"""

from __future__ import annotations

import asyncio
import os
import threading
import zipfile
from pathlib import Path
from typing import Any
from weakref import WeakKeyDictionary

from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment
from nanomuse.tools.office import resolve_inside
from nanomuse.workspace_context import active_context, active_workspace

_INPUT_LIMIT = 10 * 1024 * 1024
_EXPANDED_LIMIT = 16 * 1024 * 1024
_MEMBER_LIMIT = 8 * 1024 * 1024
_MEMBERS_LIMIT = 500
_TEXT_LIMIT = 200_000
_CELLS_LIMIT = 3_000
_QUEUE_LIMIT = 100
_SLOTS = threading.BoundedSemaphore(2)
_LOOP_SLOTS: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = WeakKeyDictionary()
_WAITING: WeakKeyDictionary[asyncio.AbstractEventLoop, int] = WeakKeyDictionary()


def _validate_archive(path: Path) -> None:
    if path.stat().st_size > _INPUT_LIMIT:
        raise ValueError("Файл больше 10 МБ; пока обработать его здесь нельзя")
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > _MEMBERS_LIMIT or sum(i.file_size for i in infos) > _EXPANDED_LIMIT:
            raise ValueError("Документ слишком велик после распаковки")
        for info in infos:
            name = info.filename.lower()
            if info.file_size > _MEMBER_LIMIT:
                raise ValueError("Часть документа слишком велика")
            if name.startswith("_xmlsignatures/"):
                raise ValueError("Подписанный документ нельзя изменить без нарушения подписи")
            if name.endswith("vbaproject.bin"):
                raise ValueError("Документы с макросами здесь пока не редактируются")
            # openpyxl does not preserve every drawing, external link or pivot
            # feature. Refuse such workbooks rather than quietly strip user content.
            if path.suffix.lower() == ".xlsx" and (
                name.startswith(("xl/drawings/", "xl/externallinks/", "xl/pivottables/", "xl/slicers/", "xl/slicercaches/"))
                or name == "xl/connections.xml"
            ):
                raise ValueError("Таблица содержит диаграммы или внешние данные, которые здесь нельзя надёжно сохранить")
            if not name.endswith((".xml", ".rels")):
                continue
            # Reject DTD/entity declarations and UTF-16/32 XML before handing an
            # untrusted archive to a parser; these are not needed for our format.
            with archive.open(info) as stream:
                tail = b""
                while chunk := stream.read(65536):
                    raw = (tail + chunk).lower()
                    if b"\x00" in raw or b"<!doctype" in raw or b"<!entity" in raw:
                        raise ValueError("Документ содержит неподдерживаемые объявления XML")
                    tail = raw[-16:]


def _path(workspace: Path, raw: str, suffix: str, *, existing: bool) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("Укажите имя файла")
    if os.environ.get("NANOMUSE_SHARED_CLOUD_BOT") == "1" and active_context() is None:
        raise PermissionError("Не удалось подтвердить пространство этой беседы")
    path = resolve_inside(workspace, [], raw, must_exist=existing, write=not existing)
    root = active_workspace(workspace)
    if os.environ.get("NANOMUSE_SHARED_CLOUD_BOT") == "1" and not path.is_relative_to(root):
        raise PermissionError("Файл должен находиться в папке этой беседы")
    if path.suffix.lower() != suffix:
        raise ValueError(f"Нужен файл {suffix}")
    if not existing and not path.is_relative_to(root):
        raise PermissionError("Результат можно сохранить только в папку этой беседы")
    if existing and not path.is_file():
        raise ValueError("Исходный файл не найден")
    if not existing and path.exists():
        raise ValueError("Файл результата уже существует; укажите другое имя")
    return path


def _blocks_size(blocks: list[dict[str, Any]]) -> int:
    if not isinstance(blocks, list) or len(blocks) > 500:
        raise ValueError("Слишком много частей документа")
    size = 0
    for b in blocks:
        if not isinstance(b, dict):
            raise ValueError("Неверная часть документа")
        text = b.get("text", "")
        if not isinstance(text, str):
            raise ValueError("Текст части должен быть строкой")
        size += len(text)
    if size > _TEXT_LIMIT:
        raise ValueError("Текст слишком большой; разделите документ на части")
    return size


def _paragraphs(document: Any) -> list[Any]:
    paragraphs = list(document.paragraphs)
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                paragraphs.extend(cell.paragraphs)
    return paragraphs


def _shown_path(dst: Path) -> str:
    context = active_context()
    return dst.relative_to(context.session).as_posix() if context is not None else dst.name


def _docx(src: Path | None, dst: Path, action: str, blocks: list[dict[str, Any]], find: str, replacement: str) -> str:
    from docx import Document

    if src:
        _validate_archive(src)
    doc = Document(str(src)) if src else Document()
    if action == "replace":
        if not find or len(find) > _TEXT_LIMIT or len(replacement) > _TEXT_LIMIT:
            raise ValueError("Укажите текст для замены и замену")
        count = 0
        for paragraph in _paragraphs(doc):
            original = paragraph.text
            if find not in original:
                continue
            # If a match crosses a formatting run (or includes a hyperlink), editing
            # runs separately would silently skip/change part of the requested replace.
            # Compare the complete text before touching the document and fail instead.
            separate = "".join(run.text.replace(find, replacement) for run in paragraph.runs)
            if separate != original.replace(find, replacement):
                raise ValueError("Текст пересекает границы форматирования; документ не изменён")
            count += original.count(find)
            for run in paragraph.runs:
                if find in run.text:
                    run.text = run.text.replace(find, replacement)
        if count == 0:
            raise ValueError("Текст не найден в одном фрагменте документа; форматирование не изменено")
    else:
        _blocks_size(blocks)
        for block in blocks:
            kind = block.get("kind", "paragraph")
            text = block.get("text", "")
            if kind == "heading":
                level = block.get("level", 1)
                if not isinstance(level, int) or isinstance(level, bool) or not 1 <= level <= 3:
                    raise ValueError("Уровень заголовка должен быть от 1 до 3")
                doc.add_heading(text, level)
            elif kind in ("paragraph", "bullet", "number"):
                style = {"paragraph": None, "bullet": "List Bullet", "number": "List Number"}[kind]
                doc.add_paragraph(text, style=style)
            else:
                raise ValueError("Неизвестный тип части документа")
        count = len(blocks)
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation avoids overwriting another request's result.
    with dst.open("xb") as out:
        try:
            doc.save(out)
        except Exception:
            out.close()
            dst.unlink(missing_ok=True)
            raise
    return f"Готово: {_shown_path(dst)}. {'Замен' if action == 'replace' else 'Частей'}: {count}."


def _cell_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, str) and len(value) > 32_000:
            raise ValueError("Значение ячейки слишком длинное")
        return value
    raise ValueError("Значение ячейки должно быть текстом, числом или логическим значением")


def _xlsx(src: Path | None, dst: Path, action: str, sheet: str, rows: list[list[Any]], cells: list[dict[str, Any]]) -> str:
    from openpyxl import Workbook, load_workbook
    from openpyxl.utils.cell import column_index_from_string, coordinate_from_string

    if src:
        _validate_archive(src)
    wb = load_workbook(str(src), keep_links=False) if src else Workbook()
    try:
        if not isinstance(sheet, str) or len(sheet) > 31:
            raise ValueError("Некорректное имя листа")
        if action == "create":
            ws = wb.active
            if sheet:
                ws.title = sheet
            if not isinstance(rows, list) or len(rows) > _CELLS_LIMIT:
                raise ValueError("Слишком много строк")
            total = 0
            text_size = 0
            for row in rows:
                if not isinstance(row, list):
                    raise ValueError("Строка таблицы должна быть списком ячеек")
                total += len(row)
                if total > _CELLS_LIMIT:
                    raise ValueError("Слишком много ячеек за один раз")
                text_size += sum(len(v) for v in row if isinstance(v, str))
                if text_size > _TEXT_LIMIT:
                    raise ValueError("Слишком много текста в таблице")
                ws.append([_cell_value(v) for v in row])
        else:
            if not isinstance(cells, list) or not cells or len(cells) > _CELLS_LIMIT:
                raise ValueError("Укажите от 1 до 3000 изменяемых ячеек")
            total = len(cells)
            text_size = 0
            for entry in cells:
                if not isinstance(entry, dict):
                    raise ValueError("Неверное описание ячейки")
                if isinstance(entry.get("value"), str):
                    text_size += len(entry["value"])
                    if text_size > _TEXT_LIMIT:
                        raise ValueError("Слишком много текста в таблице")
                sheet_name = entry.get("sheet") or sheet or wb.active.title
                if sheet_name not in wb.sheetnames:
                    raise ValueError(f"Лист {sheet_name} не найден")
                coordinate = entry.get("cell")
                if not isinstance(coordinate, str) or len(coordinate) > 10:
                    raise ValueError("Неверный адрес ячейки")
                column, row = coordinate_from_string(coordinate)
                if column_index_from_string(column) > 16384 or row > 1048576:
                    raise ValueError("Адрес ячейки за пределами Excel")
                wb[sheet_name][coordinate] = _cell_value(entry.get("value"))
        dst.parent.mkdir(parents=True, exist_ok=True)
        with dst.open("xb") as out:
            try:
                wb.save(out)
            except Exception:
                out.close()
                dst.unlink(missing_ok=True)
                raise
        return f"Готово: {_shown_path(dst)}. Ячеек: {total}. Формулы пересчитываются в Excel."
    finally:
        wb.close()


class _OfficeDocument(BaseTool):
    workspace: Path

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        return CallAssessment(risk=RiskLevel.MODERATE, summary=f"{self.name}: подготовить документ")

    async def _run(self, fn: Any, *args: Any) -> ToolResult:
        def work() -> ToolResult:
            _SLOTS.acquire()
            try:
                return ToolResult(output=fn(*args))
            except (OSError, ValueError, TypeError, PermissionError, zipfile.BadZipFile) as exc:
                return ToolResult.fail(f"Не удалось обработать документ: {exc}")
            except Exception as exc:  # noqa: BLE001 - corrupt Office data is a user-visible error
                return ToolResult.fail(f"Не удалось обработать документ: {type(exc).__name__}")
            finally:
                _SLOTS.release()

        loop = asyncio.get_running_loop()
        gate = _LOOP_SLOTS.get(loop)
        if gate is None:
            gate = asyncio.Semaphore(2)
            _LOOP_SLOTS[loop] = gate
        if _WAITING.get(loop, 0) >= _QUEUE_LIMIT:
            return ToolResult.fail("Очередь документов заполнена; повторите запрос немного позже")
        _WAITING[loop] = _WAITING.get(loop, 0) + 1
        try:
            try:
                await asyncio.wait_for(gate.acquire(), timeout=60)
            except TimeoutError:
                return ToolResult.fail("Очередь документов занята; повторите запрос немного позже")
        finally:
            _WAITING[loop] -= 1
        task = asyncio.create_task(asyncio.to_thread(work))
        try:
            # A cancelled caller must not free its slot while its worker thread still runs.
            return await asyncio.shield(task)
        finally:
            if task.done():
                gate.release()
            else:
                task.add_done_callback(lambda _task: gate.release())


class OfficeDocx(_OfficeDocument):
    name: str = "office_docx"
    description: str = (
        "Create or edit a Word .docx in this conversation without Python. "
        "Actions: create or append blocks (paragraph, heading, bullet, number); replace text "
        "within existing formatting runs. Always save to a new output path; the original stays."
    )
    parameters: dict[str, Any] = {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["create", "append", "replace"]},
        "path": {"type": "string", "description": "Existing .docx for append/replace."},
        "out": {"type": "string", "description": "New .docx path in this conversation."},
        "blocks": {"type": "array", "items": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["paragraph", "heading", "bullet", "number"]},
            "text": {"type": "string"}, "level": {"type": "integer"}}}},
        "find": {"type": "string"}, "replacement": {"type": "string"},
    }, "required": ["action", "out"]}

    async def execute(self, action: str = "", path: str = "", out: str = "", blocks: list | None = None,
                      find: str = "", replacement: str = "", **_: Any) -> ToolResult:
        try:
            if action not in ("create", "append", "replace"):
                raise ValueError("Неизвестное действие")
            if not isinstance(find, str) or not isinstance(replacement, str):
                raise ValueError("Текст замены должен быть строкой")
            src = _path(self.workspace, path, ".docx", existing=True) if action != "create" else None
            dst = _path(self.workspace, out, ".docx", existing=False)
            return await self._run(_docx, src, dst, action, blocks or [], find, replacement)
        except (OSError, ValueError, PermissionError) as exc:
            return ToolResult.fail(str(exc))


class OfficeXlsx(_OfficeDocument):
    name: str = "office_xlsx"
    description: str = (
        "Create an Excel .xlsx from rows or edit named cells without Python. "
        "Always write a new output file. Formulas are stored, not calculated on the server. "
        "Editing workbooks with charts, external links, pivots, macros or signatures is unsupported."
    )
    parameters: dict[str, Any] = {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["create", "edit"]},
        "path": {"type": "string", "description": "Existing .xlsx for edit."},
        "out": {"type": "string", "description": "New .xlsx path in this conversation."},
        "sheet": {"type": "string", "description": "Sheet name."},
        "rows": {"type": "array", "items": {"type": "array", "items": {}}},
        "cells": {"type": "array", "items": {"type": "object", "properties": {
            "sheet": {"type": "string"}, "cell": {"type": "string"}, "value": {}}}},
    }, "required": ["action", "out"]}

    async def execute(self, action: str = "", path: str = "", out: str = "", sheet: str = "",
                      rows: list | None = None, cells: list | None = None, **_: Any) -> ToolResult:
        try:
            if action not in ("create", "edit"):
                raise ValueError("Неизвестное действие")
            src = _path(self.workspace, path, ".xlsx", existing=True) if action == "edit" else None
            dst = _path(self.workspace, out, ".xlsx", existing=False)
            return await self._run(_xlsx, src, dst, action, sheet, rows or [], cells or [])
        except (OSError, ValueError, PermissionError) as exc:
            return ToolResult.fail(str(exc))
