"""Content types for the files the agent makes.

``mimetypes`` on a slim container does not know the OOXML family (`.docx`, `.xlsx`,
`.pptx`) and answers `application/octet-stream`, which makes a download land as an
unknown file. The few types a document turn actually produces are named here.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".docm": "application/vnd.ms-word.document.macroEnabled.12",
    ".doc": "application/msword",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".xls": "application/vnd.ms-excel",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".pptm": "application/vnd.ms-powerpoint.presentation.macroEnabled.12",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pdf": "application/pdf",
    ".csv": "text/csv",
    ".zip": "application/zip",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".json": "application/json",
}


def file_mime(name: str) -> str:
    """The type of a file by its name, with the OOXML family known."""
    suffix = Path(name).suffix.lower()
    return TYPES.get(suffix) or mimetypes.guess_type(name)[0] or "application/octet-stream"


__all__ = ["TYPES", "file_mime"]
