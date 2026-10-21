"""Files do not stay on a shared computer.

A runtime on the person's own machine keeps what they sent and what the agent made: the
workspace is theirs. A runtime that serves people from somewhere else (the Telegram bot, a
hosted box) is the opposite: the picture they sent is looked at once, the document they asked
for is delivered once, and after that both are somebody else's files on somebody else's disk.

``[server] keep_files_hours`` says how long such a runtime keeps them; 0, the default, keeps
them (the person's own computer). The sweep runs at start and then once an hour, so a file
that arrived at noon is gone by the time the setting says, without waiting for a restart.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from nanomuse.logger import logger

#: How often the sweep runs while the runtime is up.
SWEEP_EVERY_S = 3600.0


def sweep(workspace: Path, hours: float, now: float | None = None) -> tuple[int, int]:
    """Delete files under ``workspace`` older than ``hours``. Returns (deleted, bytes).

    Folders that end up empty go too, so the workspace does not fill with the skeleton of
    what used to be there. Symlinks are removed, never followed: a link out of the workspace
    is not ours to walk.
    """
    if hours <= 0:
        return (0, 0)
    root = workspace
    if not root.is_dir():
        return (0, 0)
    moment = time.time() if now is None else now
    cutoff = moment - hours * 3600.0
    deleted = 0
    freed = 0
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        try:
            if path.is_symlink():
                path.unlink()
                deleted += 1
                continue
            if path.is_dir():
                # only the empty ones: a folder with something new in it stays
                try:
                    path.rmdir()
                except OSError:
                    pass
                continue
            stat = path.stat()
            if stat.st_mtime > cutoff:
                continue
            size = stat.st_size
            path.unlink()
            deleted += 1
            freed += size
        except OSError as exc:  # a file another process holds, a permission we do not have
            logger.debug("housekeeping: {} stays ({})", path, exc)
    if deleted:
        logger.info(
            "housekeeping: {} file(s), {:.1f} MB, older than {:g}h removed from the workspace",
            deleted,
            freed / (1024 * 1024),
            hours,
        )
    return (deleted, freed)


async def keep_sweeping(workspace: Path, hours: float, every: float = SWEEP_EVERY_S) -> None:
    """The background half: a sweep now and then one an hour, until the runtime stops."""
    while True:
        try:
            sweep(workspace, hours)
        except Exception as exc:  # noqa: BLE001 — housekeeping must never stop the agent
            logger.warning("housekeeping failed: {}", exc)
        await asyncio.sleep(every)
