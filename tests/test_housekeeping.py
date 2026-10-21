"""Files on a shared box: kept while they are needed, gone when the setting says."""

from __future__ import annotations

import os
import time
from pathlib import Path

from nanomuse.housekeeping import sweep


def _age(path: Path, hours: float) -> None:
    old = time.time() - hours * 3600
    os.utime(path, (old, old))


def test_old_files_go_and_new_ones_stay(tmp_path: Path) -> None:
    day = tmp_path / "attachments" / "2026-10-21"
    day.mkdir(parents=True)
    old = day / "photo.jpg"
    old.write_bytes(b"x" * 100)
    fresh = day / "letter.pdf"
    fresh.write_bytes(b"y" * 10)
    _age(old, 5)

    deleted, freed = sweep(tmp_path, 1)

    assert deleted == 1 and freed == 100
    assert not old.exists() and fresh.exists()


def test_an_empty_folder_goes_too_unless_something_new_is_in_it(tmp_path: Path) -> None:
    day = tmp_path / "attachments" / "2026-10-20"
    day.mkdir(parents=True)
    (day / "old.txt").write_text("gone")
    _age(day / "old.txt", 9)

    sweep(tmp_path, 1)

    assert not day.exists()
    assert tmp_path.exists()  # the workspace itself is never removed


def test_zero_keeps_everything(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("kept")
    _age(f, 1000)

    assert sweep(tmp_path, 0) == (0, 0)
    assert f.exists()


def test_a_symlink_is_removed_and_never_followed(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-heart"
    outside.mkdir(exist_ok=True)
    (outside / "keep.txt").write_text("not ours")
    link = tmp_path / "link"
    link.symlink_to(outside)
    _age(link.parent, 5)

    sweep(tmp_path, 1)

    assert not link.exists()
    assert (outside / "keep.txt").exists()


def test_a_missing_workspace_is_not_an_error(tmp_path: Path) -> None:
    assert sweep(tmp_path / "nowhere", 1) == (0, 0)
