from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from nanomuse.schema import RiskLevel
from nanomuse.tools.office import (
    OfficeConvert,
    OfficeVba,
    com_available,
    family_of,
    has_vba_project,
    resolve_inside,
    soffice_path,
    write_bas,
)


def test_family_of_knows_the_three_suites(tmp_path: Path):
    assert family_of(Path("a/report.docx")) == "word"
    assert family_of(Path("legacy.DOC")) == "word"
    assert family_of(Path("book.xlsm")) == "excel"
    assert family_of(Path("table.csv")) == "excel"
    assert family_of(Path("deck.pptx")) == "powerpoint"
    assert family_of(Path("notes.md")) is None
    assert family_of(Path("no-suffix")) is None


def test_resolve_inside_refuses_to_leave_the_workspace(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.docx").write_bytes(b"x")
    assert resolve_inside(ws, [], "a.docx") == ws / "a.docx"
    with pytest.raises(FileNotFoundError):
        resolve_inside(ws, [], "missing.docx")
    with pytest.raises(PermissionError):
        resolve_inside(ws, [], "/etc/passwd")
    # an extra root is allowed, and so is a file that is not there yet (an output path)
    extra = tmp_path / "docs"
    extra.mkdir()
    assert (
        resolve_inside(ws, [extra], str(extra / "new.pdf"), must_exist=False) == extra / "new.pdf"
    )


def test_write_bas_is_something_office_can_import(tmp_path: Path):
    bas = write_bas(tmp_path / "book.xlsm", "Отчёт", 'Sub Hello()\n  MsgBox "привет"\nEnd Sub\n')
    assert bas.name == "book.bas"
    raw = bas.read_bytes()
    assert (
        b'Attribute VB_Name = "\xd0\x9e\xd1\x82\xd1\x87\xd1\x91\xd1\x82"' in raw
    )  # utf-8, not mangled
    assert b"\r\n" in raw  # the VBA editor wants CRLF
    text = raw.decode("utf-8")
    assert text.startswith('Attribute VB_Name = "Отчёт"')
    assert 'MsgBox "привет"' in text


def test_has_vba_project_looks_inside_the_container(tmp_path: Path):
    plain = tmp_path / "plain.xlsx"
    with zipfile.ZipFile(plain, "w") as zf:
        zf.writestr("xl/workbook.xml", "<workbook/>")
    assert has_vba_project(plain) is False
    macro = tmp_path / "macro.xlsm"
    with zipfile.ZipFile(macro, "w") as zf:
        zf.writestr("xl/workbook.xml", "<workbook/>")
        zf.writestr("xl/vbaProject.bin", b"\xd0\xcf\x11\xe0")
    assert has_vba_project(macro) is True
    # a file that is not a zip at all is not a crash
    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"not a zip")
    assert has_vba_project(broken) is False


async def test_convert_refuses_a_file_it_does_not_understand(tmp_path: Path):
    (tmp_path / "notes.md").write_text("# hi")
    tool = OfficeConvert(workspace=tmp_path)
    r = await tool.execute(path="notes.md")
    assert not r.ok and "not a Word, Excel or PowerPoint file" in r.error
    r = await tool.execute(path="missing.docx")
    assert not r.ok and "does not exist" in r.error
    r = await tool.execute(path="")
    assert not r.ok and "needs a path" in r.error


async def test_convert_assessment_is_writing_not_reading(tmp_path: Path):
    tool = OfficeConvert(workspace=tmp_path)
    assert tool.assess({"path": "a.docx", "to": "pdf"}).risk == RiskLevel.MODERATE
    assert OfficeVba(workspace=tmp_path).assess({"path": "a.xlsx"}).risk == RiskLevel.SENSITIVE


@pytest.mark.skipif(
    soffice_path() is None or com_available(), reason="no headless converter on this machine"
)
async def test_convert_makes_a_pdf_with_libreoffice(tmp_path: Path):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_heading("Отчёт", 1)
    document.add_paragraph("Проверка моста Office → PDF.")
    document.save(tmp_path / "report.docx")

    tool = OfficeConvert(workspace=tmp_path)
    r = await tool.execute(path="report.docx")
    assert r.ok, r.error
    pdf = tmp_path / "report.pdf"
    assert pdf.exists() and pdf.read_bytes()[:5] == b"%PDF-"
    assert str(pdf) in r.output
    # the source is untouched
    assert (tmp_path / "report.docx").exists()


@pytest.mark.skipif(com_available(), reason="on Windows the macro really goes into the file")
async def test_vba_falls_back_to_a_bas_file(tmp_path: Path):
    docx = pytest.importorskip("docx")
    docx.Document().save(tmp_path / "letter.docx")
    tool = OfficeVba(workspace=tmp_path)
    r = await tool.execute(
        path="letter.docx", code='Sub Hello()\n  MsgBox "привет"\nEnd Sub', module="Hello"
    )
    assert not r.ok
    assert "Microsoft Office on Windows" in r.error and "Import" in r.error
    assert (
        (tmp_path / "letter.bas")
        .read_text(encoding="utf-8")
        .startswith('Attribute VB_Name = "Hello"')
    )


def test_office_xlsm_builds_a_macro_workbook_from_the_seed(tmp_path: Path):
    """The seed's VBA project survives the data write, and the config sheet is hidden."""
    import asyncio

    from nanomuse.tools.office import OfficeXlsm

    out = tmp_path / "book.xlsm"
    tool = OfficeXlsm(workspace=tmp_path, extra_roots=[])
    result = asyncio.run(
        tool.execute(
            out=str(out),
            sheet="Данные",
            rows=[["Товар", "Цена"], ["Ручка", 120]],
            commands=[{"action": "format", "target": "Данные!A1:B2"}],
            caption="Обновить",
        )
    )
    assert result.ok, result.error
    assert has_vba_project(out)
    with zipfile.ZipFile(out) as book, zipfile.ZipFile(OfficeXlsm.SEED) as seed:
        assert book.read("xl/vbaProject.bin") == seed.read("xl/vbaProject.bin")
    import openpyxl

    workbook = openpyxl.load_workbook(out, keep_vba=True)
    assert workbook.sheetnames == ["Данные", "_cfg"]
    assert workbook["_cfg"].sheet_state == "hidden"
    assert workbook["_cfg"]["F1"].value == "Обновить"
    assert workbook["Данные"]["A2"].value == "Ручка"
