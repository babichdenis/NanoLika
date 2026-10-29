"""Context-local workspace routing for one running conversation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkspaceContext:
    thread_id: str
    session: Path
    shared: tuple[Path, ...] = ()
    legacy_attachments: frozenset[str] = frozenset()


_current: ContextVar[WorkspaceContext | None] = ContextVar(
    "nanomuse_workspace_context", default=None
)


@contextmanager
def workspace_context(
    thread_id: str,
    session: Path,
    shared: tuple[Path, ...] | list[Path] = (),
    legacy_attachments: frozenset[str] | set[str] = frozenset(),
) -> Iterator[None]:
    """Route filesystem tools to a session root and a read-only client library."""
    value = WorkspaceContext(
        thread_id=thread_id,
        session=Path(session).resolve(),
        shared=tuple(Path(root).resolve() for root in shared),
        legacy_attachments=frozenset(legacy_attachments),
    )
    token = _current.set(value)
    try:
        yield
    finally:
        _current.reset(token)


def active_context() -> WorkspaceContext | None:
    return _current.get()


def active_workspace(default: Path) -> Path:
    context = _current.get()
    return context.session if context is not None else Path(default).resolve()


def active_shared_roots(default: tuple[Path, ...] | list[Path] = ()) -> tuple[Path, ...]:
    context = _current.get()
    return (
        context.shared if context is not None else tuple(Path(root).resolve() for root in default)
    )


def namespaced_session_path(raw: str, thread_id: str, session: Path) -> Path | None:
    """Resolve ``threads/<thread-id>/<relative-path>`` for this active session only."""
    parts = Path(raw).parts
    if len(parts) < 3 or parts[0] != "threads":
        return None
    if parts[1] != thread_id:
        raise PermissionError("that file belongs to a different conversation")
    target = (session / Path(*parts[2:])).resolve()
    if target != session and session not in target.parents:
        raise PermissionError("path outside the conversation workspace")
    return target


def active_file_path(raw: str, default: Path) -> Path:
    """Resolve an attachment for the active turn without crossing its workspace boundary."""
    context = _current.get()
    if context is None:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = Path(default).resolve() / candidate
        return candidate.resolve()
    namespaced = namespaced_session_path(raw, context.thread_id, context.session)
    if namespaced is not None:
        return namespaced
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = context.session / candidate
    resolved = candidate.resolve()
    if resolved == context.session or context.session in resolved.parents:
        return resolved
    for root in context.shared:
        if resolved == root or root in resolved.parents:
            relative = resolved.relative_to(root).as_posix()
            if relative in context.legacy_attachments:
                return resolved
    raise PermissionError("attachment is outside this conversation workspace")


def thread_path(thread_id: str, relative: str) -> str:
    """Public, unambiguous path for a file owned by one conversation."""
    rel = Path(relative).as_posix().lstrip("/")
    return f"threads/{thread_id}/{rel}"


__all__ = [
    "WorkspaceContext",
    "active_file_path",
    "active_context",
    "active_shared_roots",
    "active_workspace",
    "namespaced_session_path",
    "thread_path",
    "workspace_context",
]
