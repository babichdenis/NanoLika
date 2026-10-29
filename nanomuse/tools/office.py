"""Word, Excel and PowerPoint files: PDF out of them, and VBA into them.

Making a document needs no Office at all — OOXML is a zip of XML, and the office
skills do it with ``python-docx``, ``openpyxl`` and ``python-pptx``. Two things do
need the real application, and this is the bridge to it:

* :class:`OfficeConvert` — PDF (and the formats a converter knows) out of a document.
  On Windows Microsoft Office does it over COM, driven by a VBScript run with
  ``cscript``, so no Python package is needed; elsewhere (or when Office is missing)
  ``soffice --headless`` from LibreOffice does it. This is why the desktop build no
  longer carries the 157 MB LibreOffice: the machine's own Office is enough.
* :class:`OfficeVba` — a VBA module written into a macro-enabled workbook. Only
  Microsoft Office can do that (its VBA project object model needs one switch turned
  on, see :data:`VBA_ACCESS_HINT`); everywhere else the module is written as a
  ``.bas`` file to import by hand.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment
from nanomuse.workspace_context import (
    active_context,
    active_shared_roots,
    active_workspace,
    namespaced_session_path,
)

#: how long one document may take; a 300-page deck with pictures on a slow laptop is
#: minutes, a hang is forever
TIMEOUT_S = 240

#: which application family a file belongs to, by its suffix
FAMILIES: dict[str, tuple[str, ...]] = {
    "word": (".doc", ".docx", ".docm", ".rtf", ".txt", ".odt", ".wps"),
    "excel": (".xls", ".xlsx", ".xlsm", ".xlsb", ".csv", ".ods", ".et"),
    "powerpoint": (".ppt", ".pptx", ".pptm", ".odp", ".dps"),
}

#: the code each application uses for "save as this format" (its own enum, not ours);
#: PDF is here only where the app saves it through the ordinary save call
SAVE_AS: dict[str, dict[str, int]] = {
    "word": {"pdf": 17, "docx": 16, "doc": 0, "rtf": 6, "txt": 2, "htm": 8, "html": 8},
    "excel": {"pdf": 0, "xlsx": 51, "xls": -4143, "csv": 6, "htm": 44, "html": 44},
    "powerpoint": {"pdf": 32, "pptx": 24, "ppt": 1},
}

#: macro-enabled containers, and the code that saves one
MACRO_SUFFIX = {"word": ".docm", "excel": ".xlsm", "powerpoint": ".pptm"}
MACRO_SAVE_AS = {"word": 13, "excel": 52, "powerpoint": 25}

VBA_ACCESS_HINT = (
    "Excel/Word must allow it once: File → Options → Trust Center → Trust Center "
    "Settings → Macro Settings → «Trust access to the VBA project object model»"
)

#: VBScript helpers every generated script ends with
VBS_CHECK = """
Sub Check(what)
  If Err.Number <> 0 Then
    WScript.Echo "ERR " & what & ": " & Err.Description
    WScript.Quit 3
  End If
End Sub
"""


# --------------------------------------------------------------------------- probing
def family_of(path: Path) -> str | None:
    """``word`` / ``excel`` / ``powerpoint`` for a document, ``None`` for anything else."""
    suffix = path.suffix.lower()
    for family, suffixes in FAMILIES.items():
        if suffix in suffixes:
            return family
    return None


def com_available() -> bool:
    """Can Microsoft Office be driven over COM here? Only Windows can."""
    return sys.platform == "win32"


def soffice_path() -> str | None:
    """The LibreOffice program, if this machine has one."""
    named = os.environ.get("SOFFICE_PATH", "").strip()
    if named and Path(named).exists():
        return named
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in (
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        "/usr/lib/libreoffice/program/soffice",
        "/usr/local/bin/soffice",
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ):
        if Path(candidate).exists():
            return candidate
    return None


def converters() -> list[str]:
    """What this machine can convert with, in the order it should be tried."""
    order: list[str] = []
    if com_available():
        order.append("Microsoft Office")
    if soffice_path():
        order.append("LibreOffice")
    return order


# --------------------------------------------------------------------------- plumbing
def _quote(value: str | Path) -> str:
    """A path as a VBScript string literal."""
    return '"' + str(value).replace('"', '""') + '"'


def _run(cmd: list[str], timeout: float = TIMEOUT_S) -> tuple[int, str]:
    """Run a program, return its code and everything it said."""
    flags = 0
    if sys.platform == "win32":  # no console window flashing up in front of the user
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=flags,
        )
    except FileNotFoundError:
        return 127, f"{cmd[0]} was not found"
    except subprocess.TimeoutExpired:
        return 124, f"{cmd[0]} did not finish in {timeout:.0f} s"
    except OSError as exc:
        return 126, f"{cmd[0]} could not be started: {exc}"
    said = "\n".join(part.strip() for part in (proc.stdout, proc.stderr) if part and part.strip())
    return proc.returncode, said


def _run_vbs(body: str, timeout: float = TIMEOUT_S) -> tuple[bool, str]:
    """Run a VBScript with ``cscript`` and tell whether it said ``OK``."""
    if not com_available():
        return False, "Microsoft Office can only be driven on Windows"
    folder = Path(tempfile.mkdtemp(prefix="nanomuse-office-"))
    try:
        script = folder / "work.vbs"
        # Windows Script Host only reads a script as ANSI or UTF-16: a UTF-8 BOM is a
        # compile error, and ANSI would mangle every non-Latin path (C:\\Отчёты\\…)
        script.write_text(body, encoding="utf-16")
        code, said = _run(["cscript", "//nologo", "//T:" + str(int(timeout)), str(script)], timeout)
        last = said.strip().splitlines()[-1].strip() if said.strip() else ""
        if code == 0 and last == "OK":
            return True, said
        detail = said.strip() or f"cscript exited with {code} and said nothing"
        return False, detail.replace("\r", " ").strip()
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _check_output(dst: Path) -> tuple[bool, str]:
    """Did the converter really write a file, and is it not empty?"""
    if not dst.exists():
        return False, f"{dst.name} was not written"
    size = dst.stat().st_size
    if size < 64:
        return False, f"{dst.name} came out empty ({size} bytes)"
    return True, _size(size)


def _size(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB" if n >= 1024 * 1024 else f"{max(1, n // 1024)} KB"


# --------------------------------------------------------------------------- conversion
def convert_with_office(src: Path, dst: Path) -> tuple[bool, str]:
    """Convert through Microsoft Office over COM (Windows)."""
    family = family_of(src)
    if family is None:
        return False, f"Microsoft Office has no idea what {src.suffix or 'a file'} is"
    target = dst.suffix.lower().lstrip(".")
    code = SAVE_AS[family].get(target)
    if code is None:
        return False, f"Microsoft Office cannot save a {family} file as .{target}"

    src_s, dst_s = _quote(src), _quote(dst)
    lines: list[str] = ["On Error Resume Next", "Dim app, doc, proj"]
    if family == "word":
        lines += [
            'Set app = CreateObject("Word.Application")',
            'Check "start Word"',
            "app.Visible = False",
            "app.DisplayAlerts = 0",
            f"Set doc = app.Documents.Open({src_s}, False, True)",
            'Check "open the document"',
        ]
        if target == "pdf":
            lines += [
                f"doc.ExportAsFixedFormat {dst_s}, 17",
                'Check "write the PDF"',
            ]
        else:
            lines += [f"doc.SaveAs2 {dst_s}, {code}", 'Check "save the document"']
        lines += ["doc.Close 0", "app.Quit", 'Check "close Word"']
    elif family == "excel":
        lines += [
            'Set app = CreateObject("Excel.Application")',
            'Check "start Excel"',
            "app.Visible = False",
            "app.DisplayAlerts = False",
            f"Set doc = app.Workbooks.Open({src_s}, 0, True)",
            'Check "open the workbook"',
        ]
        if target == "pdf":
            lines += [f"doc.ExportAsFixedFormat 0, {dst_s}", 'Check "write the PDF"']
        else:
            lines += [f"doc.SaveAs {dst_s}, {code}", 'Check "save the workbook"']
        lines += ["doc.Close False", "app.Quit", 'Check "close Excel"']
    else:
        # PowerPoint refuses to be invisible; it opens without a window instead
        lines += [
            'Set app = CreateObject("PowerPoint.Application")',
            'Check "start PowerPoint"',
            f"Set doc = app.Presentations.Open({src_s}, True, False, False)",
            'Check "open the presentation"',
            f"doc.SaveAs {dst_s}, {code}",
            'Check "save the presentation"',
            "doc.Close",
            "app.Quit",
            'Check "close PowerPoint"',
        ]
    lines.append('WScript.Echo "OK"')
    ok, said = _run_vbs("\n".join(lines) + VBS_CHECK)
    if ok:
        return True, "Microsoft Office opened it invisibly"
    if "принтер" in said.lower() or "printer" in said.lower():
        said += (
            ": Excel and Word need at least one printer driver to export a PDF; "
            "«Microsoft Print to PDF» is enough"
        )
    return False, said


def convert_with_soffice(src: Path, dst: Path) -> tuple[bool, str]:
    """Convert through LibreOffice's command line, headless and with its own profile."""
    exe = soffice_path()
    if not exe:
        return False, "LibreOffice is not installed"
    target = dst.suffix.lower().lstrip(".")
    work = Path(tempfile.mkdtemp(prefix="nanomuse-soffice-"))
    profile = work / "profile"
    try:
        cmd = [
            exe,
            "--headless",
            "--norestore",
            "--nolockcheck",
            "--nodefault",
            f"-env:UserInstallation={profile.as_uri()}",
            "--convert-to",
            target,
            "--outdir",
            str(work),
            str(src),
        ]
        code, said = _run(cmd)
        produced = work / (src.stem + "." + target)
        if not produced.exists():
            detail = said or f"soffice exited with {code} and wrote nothing"
            return False, detail.replace("\n", " ")[:400]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(produced, dst)
        return True, "LibreOffice converted it headlessly"
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------------------- VBA
def _vbs_vba(src: Path, dst: Path, family: str, module: str, where: str, code_file: Path) -> str:
    """The VBScript that opens a document, writes a VBA module into it and saves it."""
    lines = [
        "On Error Resume Next",
        "Dim app, doc, proj, comp, fso, code",
        'Set fso = CreateObject("Scripting.FileSystemObject")',
        # 1 = read, -1 = the file is UTF-16, so Cyrillic in the code survives
        f"code = fso.OpenTextFile({_quote(code_file)}, 1, False, -1).ReadAll",
        'Check "read the module"',
    ]
    if family == "word":
        lines += [
            'Set app = CreateObject("Word.Application")',
            'Check "start Word"',
            "app.Visible = False",
            "app.DisplayAlerts = 0",
            f"Set doc = app.Documents.Open({_quote(src)}, False, False)",
            'Check "open the document"',
            "Set proj = doc.VBProject",
        ]
        owner = "ThisDocument"
    elif family == "excel":
        lines += [
            'Set app = CreateObject("Excel.Application")',
            'Check "start Excel"',
            "app.Visible = False",
            "app.DisplayAlerts = False",
            f"Set doc = app.Workbooks.Open({_quote(src)})",
            'Check "open the workbook"',
            "Set proj = doc.VBProject",
        ]
        owner = "ThisWorkbook"
    else:
        lines += [
            'Set app = CreateObject("PowerPoint.Application")',
            'Check "start PowerPoint"',
            f"Set doc = app.Presentations.Open({_quote(src)}, False, False, False)",
            'Check "open the presentation"',
            "Set proj = doc.VBProject",
        ]
        owner = "ThisPresentation"
    lines += [
        "If Err.Number <> 0 Then",
        '  WScript.Echo "ERR vba: " & Err.Description & " | " & "' + VBA_ACCESS_HINT + '"',
        "  WScript.Quit 4",
        "End If",
        "Err.Clear",
    ]
    if where == "workbook":
        lines += [
            f"Set comp = proj.VBComponents({_quote(owner)})",
            'Check "find the document\'s own module"',
            "comp.CodeModule.AddFromString code",
            'Check "write the code"',
        ]
    else:
        lines += [
            f"proj.VBComponents.Remove proj.VBComponents({_quote(module)})",
            "Err.Clear",
            "Set comp = proj.VBComponents.Add(1)",
            'Check "add a module"',
            f"comp.Name = {_quote(module)}",
            'Check "name the module"',
            "comp.CodeModule.AddFromString code",
            'Check "write the code"',
        ]
    if family == "word":
        lines += [
            f"doc.SaveAs2 {_quote(dst)}, {MACRO_SAVE_AS['word']}",
            'Check "save the macro-enabled document"',
            "doc.Close 0",
        ]
    elif family == "excel":
        lines += [
            f"doc.SaveAs {_quote(dst)}, {MACRO_SAVE_AS['excel']}",
            'Check "save the macro-enabled workbook"',
            "doc.Close False",
        ]
    else:
        lines += [
            f"doc.SaveAs {_quote(dst)}, {MACRO_SAVE_AS['powerpoint']}",
            'Check "save the macro-enabled presentation"',
            "doc.Close",
        ]
    lines += ["app.Quit", 'Check "close the application"', 'WScript.Echo "OK"']
    return "\n".join(lines) + VBS_CHECK


def write_bas(dst: Path, module: str, code: str) -> Path:
    """The module as a ``.bas`` file, ready for File → Import in the VBA editor."""
    bas = dst.with_suffix(".bas")
    text = f'Attribute VB_Name = "{module}"\r\n' + code.strip().replace("\n", "\r\n") + "\r\n"
    bas.write_text(text, encoding="utf-8")
    return bas


def has_vba_project(path: Path) -> bool:
    """Is there really a ``vbaProject.bin`` inside the (zip) container?"""
    import zipfile

    try:
        with zipfile.ZipFile(path) as zf:
            return any(name.lower().endswith("vbaproject.bin") for name in zf.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


# --------------------------------------------------------------------------- the tools
def resolve_inside(
    workspace: Path,
    extra_roots: list[Path],
    raw: str,
    must_exist: bool = True,
    *,
    write: bool = False,
) -> Path:
    """The path, made absolute inside the workspace or one of its extra roots."""
    context = active_context()
    workspace = active_workspace(workspace)
    if context is not None:
        namespaced = namespaced_session_path(raw, context.thread_id, workspace)
        if namespaced is not None:
            if must_exist and not namespaced.exists():
                raise FileNotFoundError(f"'{raw}' does not exist")
            return namespaced
        if raw in context.legacy_attachments:
            for root in active_shared_roots([*extra_roots]):
                legacy = (root / raw).resolve()
                if legacy.is_file() and root in legacy.parents:
                    if write:
                        raise PermissionError("legacy attachments are read-only")
                    return legacy
    candidate = Path(os.path.expanduser((raw or "").strip()))
    if not candidate.is_absolute():
        candidate = workspace / candidate
    resolved = candidate.resolve()
    if context is not None:
        if resolved == workspace or workspace in resolved.parents:
            if write or resolved.exists():
                if must_exist and not resolved.exists():
                    raise FileNotFoundError(f"'{raw}' does not exist")
                return resolved
        shared_roots = active_shared_roots([*extra_roots])
        for root in shared_roots:
            if resolved == root or root in resolved.parents:
                relative = resolved.relative_to(root).as_posix() if resolved != root else ""
                if write:
                    raise PermissionError("the shared client library and extra roots are read-only")
                if relative == "threads" or relative.startswith("threads/"):
                    raise PermissionError("conversation workspaces are private")
                if relative == "attachments" or relative.startswith("attachments/"):
                    if relative not in context.legacy_attachments:
                        raise PermissionError(
                            "legacy attachments are private to their conversation"
                        )
                if must_exist and not resolved.exists():
                    raise FileNotFoundError(f"'{raw}' does not exist")
                return resolved
        raise PermissionError(f"path '{raw}' is outside this conversation workspace")
    roots = [workspace.resolve(), *[r.resolve() for r in extra_roots]]
    if not any(resolved == r or r in resolved.parents for r in roots):
        raise PermissionError(
            f"path '{raw}' is outside the workspace ({workspace}); ask the user to add it "
            "to agent.extra_roots if it is really needed"
        )
    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"'{raw}' does not exist")
    return resolved


class OfficeConvert(BaseTool):
    name: str = "office_convert"
    description: str = (
        "Turn a Word, Excel or PowerPoint file into another format with the office suite "
        "that is on this machine: Microsoft Office over COM on Windows, LibreOffice "
        "elsewhere. Mostly used for `pdf`: a report the user can send, a workbook that "
        "must not be edited, slides to print. Also handles the legacy formats that the "
        "Python libraries cannot read (`.doc`, `.xls`, `.ppt`). `path` is the document, "
        "`to` the target format (default `pdf`), `out` an optional output path (default: "
        "the same name with the new suffix, next to the original). The original is never "
        "changed. Creating and editing documents needs no converter: use the "
        "office-docx / office-xlsx / office-pptx skills."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "The document to convert."},
            "to": {
                "type": "string",
                "description": "Target format: pdf (default), docx, xlsx, pptx, csv, txt, html…",
            },
            "out": {"type": "string", "description": "Where to write the result."},
        },
        "required": ["path"],
    }
    risk: RiskLevel = RiskLevel.SAFE
    workspace: Path = Path(".")
    extra_roots: list[Path] = []

    def _resolve(self, raw: str, must_exist: bool = True, *, write: bool = False) -> Path:
        return resolve_inside(self.workspace, self.extra_roots, raw, must_exist, write=write)

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        target = str(args.get("path") or "")
        to = str(args.get("to") or "pdf")
        try:
            inside = (
                active_workspace(self.workspace) in self._resolve(target, must_exist=False).parents
            )
        except Exception:  # noqa: BLE001 - a path that cannot be resolved triggers a prompt
            inside = False
        return CallAssessment(
            risk=RiskLevel.MODERATE if inside else RiskLevel.SENSITIVE,
            summary=f"office_convert {target} → {to}",
        )

    async def execute(self, path: str = "", to: str = "pdf", out: str = "", **_: Any) -> ToolResult:
        if not path.strip():
            return ToolResult.fail("office_convert needs a path to the document")
        try:
            src = self._resolve(path)
        except (PermissionError, FileNotFoundError) as exc:
            return ToolResult.fail(str(exc))
        family = family_of(src)
        if family is None:
            return ToolResult.fail(
                f"{src.name} is not a Word, Excel or PowerPoint file (suffix "
                f"'{src.suffix or 'none'}'); the converter only knows those"
            )
        target = (to or "pdf").strip().lower().lstrip(".")
        if not target.isalnum():
            return ToolResult.fail(f"'{to}' is not a format")
        if out.strip():
            dst = self._resolve(out, must_exist=False, write=True)
        elif active_context() is not None and not src.is_relative_to(
            active_workspace(self.workspace)
        ):
            dst = active_workspace(self.workspace) / (src.stem + "." + target)
        else:
            dst = src.with_suffix("." + target)
        if dst == src:
            return ToolResult.fail("the target is the file itself; give another `to` or `out`")
        dst.parent.mkdir(parents=True, exist_ok=True)

        tried: list[str] = []
        started = time.monotonic()

        async def attempt(name: str, fn: Any) -> ToolResult | None:
            ok, said = await asyncio.to_thread(fn, src, dst)
            if ok:
                good, size = _check_output(dst)
                if not good:
                    tried.append(f"{name}: {size}")
                    return None
                seconds = time.monotonic() - started
                return ToolResult(
                    output=(
                        f"{dst.suffix.lstrip('.').upper()}: {dst} ({size}) via {name} "
                        f"in {seconds:.0f} s"
                    )
                )
            tried.append(f"{name}: {said}")
            return None

        if com_available():
            done = await attempt("Microsoft Office", convert_with_office)
            if done is not None:
                return done
        done = await attempt("LibreOffice", convert_with_soffice)
        if done is not None:
            return done
        return ToolResult.fail(
            f"could not convert {src.name} to .{target}. Tried: " + "; ".join(tried) + ". "
            "Install Microsoft Office (Windows) or LibreOffice, or set SOFFICE_PATH."
        )


class OfficeVba(BaseTool):
    name: str = "office_vba"
    description: str = (
        "Put a VBA macro into a Word, Excel or PowerPoint file. `path` is the document, "
        "`code` the VBA source (a `Sub`/`Function`, macros and `CodeModule` calls allowed), "
        "`module` its module name (default Module1), `where` = `module` for a new standard "
        "module or `workbook` to append to the document's own module (ThisWorkbook / "
        "ThisDocument). The result is macro-enabled (.xlsm/.docm/.pptm) at `out`, and the "
        "same code is written next to it as a `.bas` file for File → Import. Writing macros "
        "needs Microsoft Office on Windows; elsewhere only the `.bas` file is produced. The "
        "macro is never run: the user opens the file and runs it."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "The document to get the macro."},
            "code": {"type": "string", "description": "The VBA source of the module."},
            "module": {"type": "string", "description": "Module name (default Module1)."},
            "where": {
                "type": "string",
                "enum": ["module", "workbook"],
                "description": "A new standard module (default) or the document's own module.",
            },
            "out": {"type": "string", "description": "Where to write the macro-enabled file."},
        },
        "required": ["path", "code"],
    }
    risk: RiskLevel = RiskLevel.MODERATE
    workspace: Path = Path(".")
    extra_roots: list[Path] = []

    def _resolve(self, raw: str, must_exist: bool = True, *, write: bool = False) -> Path:
        return resolve_inside(self.workspace, self.extra_roots, raw, must_exist, write=write)

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        target = str(args.get("path") or "")
        return CallAssessment(
            risk=RiskLevel.SENSITIVE,
            summary=f"office_vba {target} ({len(str(args.get('code') or ''))} chars)",
            warnings=["code goes into a document the user may run later"],
        )

    async def execute(
        self,
        path: str = "",
        code: str = "",
        module: str = "Module1",
        where: str = "module",
        out: str = "",
        **_: Any,
    ) -> ToolResult:
        if not path.strip() or not code.strip():
            return ToolResult.fail("office_vba needs a path and the code of the module")
        try:
            src = self._resolve(path)
        except (PermissionError, FileNotFoundError) as exc:
            return ToolResult.fail(str(exc))
        family = family_of(src)
        if family is None:
            return ToolResult.fail(f"{src.name} is not a Word, Excel or PowerPoint file")
        module = (module or "Module1").strip() or "Module1"
        if not module.replace("_", "").isalnum():
            return ToolResult.fail(f"'{module}' is not a module name")
        where = "workbook" if where == "workbook" else "module"
        macro_suffix = MACRO_SUFFIX[family]
        if out.strip():
            dst = self._resolve(out, must_exist=False, write=True)
        elif active_context() is not None and not src.is_relative_to(
            active_workspace(self.workspace)
        ):
            dst = active_workspace(self.workspace) / (src.stem + macro_suffix)
        elif src.suffix.lower() == macro_suffix:
            dst = src  # their macro-enabled file: the macro goes into it
        else:
            dst = src.with_suffix(macro_suffix)
        if active_context() is not None and not dst.is_relative_to(
            active_workspace(self.workspace)
        ):
            return ToolResult.fail("the shared client library is read-only")
        dst.parent.mkdir(parents=True, exist_ok=True)

        if not com_available():
            bas = write_bas(dst, module, code)
            return ToolResult.fail(
                f"VBA needs Microsoft Office on Windows, and this machine cannot drive it. "
                f"The module was written to {bas}: open {src.name} in Excel/Word and use "
                f"Alt+F11 → File → Import File. ({VBA_ACCESS_HINT} is only needed for "
                "programmatic access, not for importing by hand.)"
            )

        work = Path(tempfile.mkdtemp(prefix="nanomuse-vba-"))
        try:
            code_file = work / "module.vba"
            code_file.write_text(code.strip() + "\r\n", encoding="utf-16")
            target = work / ("in" + src.suffix.lower())
            shutil.copyfile(src, target)
            script = _vbs_vba(target, dst, family, module, where, code_file)
            ok, said = await asyncio.to_thread(_run_vbs, script)
            if not ok:
                return ToolResult.fail(f"VBA was not written: {said}")
        finally:
            shutil.rmtree(work, ignore_errors=True)

        good, size = _check_output(dst)
        if not good:
            return ToolResult.fail(f"the macro was written but {size}")
        bas = write_bas(dst, module, code)
        macro = has_vba_project(dst)
        return ToolResult(
            output=(
                f"{dst} ({size}) now carries the module «{module}»"
                + (" with its VBA project" if macro else "")
                + f" (written into {where}"
                + ("s own module)" if where == "workbook" else ", a new standard module)")
                + f", and {bas.name} holds the same code for File → Import. "
                "Nothing was run: the user opens the file and runs the macro themselves."
            )
        )


__all__ = [
    "OfficeConvert",
    "OfficeVba",
    "com_available",
    "convert_with_office",
    "convert_with_soffice",
    "converters",
    "resolve_inside",
    "family_of",
    "has_vba_project",
    "soffice_path",
    "write_bas",
]
