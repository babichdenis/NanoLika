"""Structured cloud documents cannot run code or cross conversation workspaces."""

from __future__ import annotations

import asyncio
import threading
import time
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from test_channels_manager import FakeChannel, chan, tc, thread_id_for, wait_for  # noqa: F401

from nanomuse.tools.office_docs import OfficeDocx, OfficeXlsx
from nanomuse.workspace_context import workspace_context


@pytest.fixture()
def clients(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    return a, b


async def test_word_create_and_replace_in_own_workspace(clients):
    from docx import Document

    a, b = clients
    tool = OfficeDocx(workspace=a.parent)
    with workspace_context("a", a):
        result = await tool.execute(action="create", out="report.docx", blocks=[
            {"kind": "heading", "text": "Отчёт", "level": 1},
            {"kind": "paragraph", "text": "Сумма: 100 рублей"},
        ])
        assert result.ok, result.error
        result = await tool.execute(action="replace", path="report.docx", out="edited.docx",
                                    find="100", replacement="200")
        assert result.ok, result.error
    assert Document(a / "report.docx").paragraphs[1].text == "Сумма: 100 рублей"
    assert Document(a / "edited.docx").paragraphs[1].text == "Сумма: 200 рублей"
    with workspace_context("b", b):
        refused = await tool.execute(action="replace", path=str(a / "report.docx"), out="stolen.docx",
                                     find="100", replacement="0")
        assert not refused.ok
    assert not (b / "stolen.docx").exists()


async def test_workbook_create_and_edit_formulas_and_isolation(clients):
    from openpyxl import load_workbook

    a, b = clients
    tool = OfficeXlsx(workspace=a.parent)
    with workspace_context("a", a):
        result = await tool.execute(action="create", out="budget.xlsx", sheet="Бюджет", rows=[
            ["Статья", "Сумма"], ["Тест", 100], ["Итого", "=SUM(B2:B2)"],
        ])
        assert result.ok, result.error
        result = await tool.execute(action="edit", path="budget.xlsx", out="edited.xlsx", cells=[
            {"sheet": "Бюджет", "cell": "B2", "value": 250},
        ])
        assert result.ok, result.error
    book = load_workbook(a / "edited.xlsx")
    assert book["Бюджет"]["B2"].value == 250
    assert book["Бюджет"]["B3"].value == "=SUM(B2:B2)"
    book.close()
    with workspace_context("b", b):
        refused = await tool.execute(action="edit", path=str(a / "budget.xlsx"), out="stolen.xlsx",
                                     cells=[{"cell": "A1", "value": "secret"}])
        assert not refused.ok
    assert not (b / "stolen.xlsx").exists()


@pytest.mark.parametrize("tool_type,suffix", [(OfficeDocx, ".docx"), (OfficeXlsx, ".xlsx")])
async def test_refuses_symlink_escape_and_missing_context(clients, tool_type, suffix):
    a, b = clients
    (a / "escape").symlink_to(b, target_is_directory=True)
    tool = tool_type(workspace=a.parent)
    no_context = await tool.execute(action="create", out="unsafe" + suffix)
    assert not no_context.ok
    with workspace_context("a", a):
        result = await tool.execute(action="create", out="escape/unsafe" + suffix)
        assert not result.ok
    assert not (b / ("unsafe" + suffix)).exists()


@pytest.mark.parametrize("suffix,tool_type", [(".docx", OfficeDocx), (".xlsx", OfficeXlsx)])
async def test_rejects_unsafe_xml_and_expanding_archives(clients, suffix, tool_type):
    a, _ = clients
    tool = tool_type(workspace=a.parent)
    actions = {"action": "replace", "find": "x", "replacement": "y"} if suffix == ".docx" else {
        "action": "edit", "cells": [{"cell": "A1", "value": "x"}],
    }
    with workspace_context("a", a):
        with zipfile.ZipFile(a / ("unsafe" + suffix), "w") as z:
            z.writestr("a.xml", '<!DOCTYPE foo [ <!ENTITY x "y"> ]><foo>&x;</foo>')
        result = await tool.execute(path="unsafe" + suffix, out="out" + suffix, **actions)
        assert not result.ok and "XML" in result.error
        with zipfile.ZipFile(a / ("large" + suffix), "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("big.xml", b"x" * (9 * 1024 * 1024))
        result = await tool.execute(path="large" + suffix, out="out" + suffix, **actions)
        assert not result.ok and "велика" in result.error
    assert not (a / ("out" + suffix)).exists()


async def test_refuses_overwrite_and_cross_run_replacement(clients):
    from docx import Document

    a, _ = clients
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("Hel").bold = True
    paragraph.add_run("lo")
    document.save(a / "in.docx")
    tool = OfficeDocx(workspace=a.parent)
    with workspace_context("a", a):
        refused = await tool.execute(action="replace", path="in.docx", out="out.docx", find="Hello", replacement="Bye")
        assert not refused.ok and "форматирования" in refused.error
        refused = await tool.execute(action="create", out="in.docx")
        assert not refused.ok and "существует" in refused.error
    assert Document(a / "in.docx").paragraphs[0].text == "Hello"
    assert not (a / "out.docx").exists()


async def test_replacement_does_not_partially_skip_formatted_text(clients):
    from docx import Document

    a, _ = clients
    document = Document()
    document.add_paragraph("Hello")
    split = document.add_paragraph()
    split.add_run("Hel").bold = True
    split.add_run("lo")
    document.save(a / "in.docx")
    tool = OfficeDocx(workspace=a.parent)
    with workspace_context("a", a):
        result = await tool.execute(action="replace", path="in.docx", out="out.docx", find="Hello", replacement="Bye")
    assert not result.ok and "форматирования" in result.error
    assert not (a / "out.docx").exists()
    assert [p.text for p in Document(a / "in.docx").paragraphs] == ["Hello", "Hello"]


async def test_rejects_chart_workbook_instead_of_silently_changing_it(clients):
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference

    a, _ = clients
    workbook = Workbook()
    sheet = workbook.active
    sheet.append([1, 2])
    chart = BarChart()
    chart.add_data(Reference(sheet, min_col=1, max_col=2, min_row=1))
    sheet.add_chart(chart, "D1")
    workbook.save(a / "chart.xlsx")
    workbook.close()
    tool = OfficeXlsx(workspace=a.parent)
    with workspace_context("a", a):
        result = await tool.execute(action="edit", path="chart.xlsx", out="out.xlsx", cells=[{"cell": "A1", "value": 3}])
    assert not result.ok and "диаграммы" in result.error
    assert not (a / "out.xlsx").exists()


async def test_document_queue_limits_parallel_work_without_losing_requests(clients):
    a, _ = clients
    tool = OfficeDocx(workspace=a)
    lock = threading.Lock()
    active = peak = 0

    def work(index):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        return str(index)

    results = await asyncio.gather(*(tool._run(work, i) for i in range(12)))
    assert all(result.ok for result in results)
    assert [r.output for r in results] == [str(i) for i in range(12)]
    assert peak == 2


async def test_cancelled_document_keeps_slot_until_worker_finishes(clients, monkeypatch):
    from nanomuse.tools import office_docs

    a, _ = clients
    tool = OfficeDocx(workspace=a)
    started = asyncio.Event()
    loop = asyncio.get_running_loop()
    release = threading.Event()
    lock = threading.Lock()
    active = 0
    third_started = threading.Event()

    def work(index):
        nonlocal active
        with lock:
            active += 1
            if active == 2:
                loop.call_soon_threadsafe(started.set)
        if index == 3:
            third_started.set()
        release.wait(5)
        with lock:
            active -= 1
        return str(index)

    first = asyncio.create_task(tool._run(work, 1))
    second = asyncio.create_task(tool._run(work, 2))
    third = None
    try:
        await asyncio.wait_for(started.wait(), 2)
        monkeypatch.setattr(office_docs, "_QUEUE_LIMIT", 1)
        third = asyncio.create_task(tool._run(work, 3))
        for _ in range(100):
            if office_docs._WAITING.get(loop) == 1:
                break
            await asyncio.sleep(0.01)
        assert office_docs._WAITING.get(loop) == 1
        overflow = await tool._run(work, 4)
        assert not overflow.ok and "заполнена" in overflow.error
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        await asyncio.sleep(0.05)
        assert not third_started.is_set()
    finally:
        release.set()
        await asyncio.gather(first, second, *([third] if third else []), return_exceptions=True)
    assert third_started.is_set()


def test_shared_bot_registers_structured_office_tools(settings, monkeypatch):
    from nanomuse.llm import MockLLM
    from nanomuse.server.service import MuseService

    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    settings.agent.tool_search = True
    service = MuseService(settings, llm=MockLLM([]))
    assert service.app.tools.get("office_docx") is not None
    assert service.app.tools.get("office_xlsx") is not None
    assert {"office_docx", "office_xlsx"}.issubset(service.app.tools.visible)


@pytest.mark.parametrize("channel", ["telegram", "max"])
@pytest.mark.parametrize("kind", ["docx", "xlsx"])
def test_messenger_document_is_edited_and_returned(chan, monkeypatch, channel, kind):  # noqa: F811
    from docx import Document
    from openpyxl import Workbook, load_workbook

    from nanomuse.channels.base import InboundFile, InboundMessage
    from nanomuse.memory.scope import account_scope
    from nanomuse.schema import LLMResponse

    _client, service, llm, manager, fake = chan
    monkeypatch.setenv("NANOMUSE_SHARED_CLOUD_BOT", "1")
    service.app.tools.add(OfficeDocx(workspace=service.workspace()), OfficeXlsx(workspace=service.workspace()))
    service.app.sentinel.settings.always_allow_tools.extend(["office_docx", "office_xlsx"])
    manager.channels[channel] = fake
    manager.types[channel] = FakeChannel
    manager.pairing.add(channel, "u1", sender_name="Ann", chat_id="chat1")
    manager._cloud_customer_scopes[(channel, "u1")] = account_scope("customer-a")
    buffer = BytesIO()
    if kind == "docx":
        document = Document()
        document.add_paragraph("Цена 100")
        document.save(buffer)
    else:
        workbook = Workbook()
        workbook.active["A1"] = 100
        workbook.save(buffer)
        workbook.close()

    def edit(_messages):
        thread = service.threads[thread_id_for(channel, "chat1")]
        incoming = next(e for e in reversed(thread.timeline.events) if e.get("files"))
        path = incoming["files"][0]["path"]
        if kind == "docx":
            call = tc("office_docx", action="replace", path=path, out="result.docx", find="100", replacement="200")
        else:
            call = tc("office_xlsx", action="edit", path=path, out="result.xlsx", cells=[{"cell": "A1", "value": 200}])
        return LLMResponse(tool_calls=[call])

    llm.script.extend([edit, LLMResponse(content=f"Готово: result.{kind}")])
    fake.bus.inbound(InboundMessage(
        channel=channel, chat_id="chat1", sender_id="u1", text="Исправь файл",
        files=[InboundFile(f"input.{kind}", buffer.getvalue(), "file")],
    ))
    sent = wait_for(lambda: next((item for item in fake.sent if item[2]), None))
    assert sent[0] == "chat1"
    path = Path(sent[2][0])
    if kind == "docx":
        assert Document(path).paragraphs[0].text == "Цена 200"
    else:
        result = load_workbook(path)
        assert result.active["A1"].value == 200
        result.close()
