"""Render the brand's SVG jobs with an existing Sharp installation."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def render_jobs(jobs: list[tuple[Path, str, int]]) -> None:
    """Use a local Node Sharp, without downloading a browser or packages."""
    data = [{"path": str(path), "svg": svg, "size": px} for path, svg, px in jobs]
    subprocess.run(
        [os.environ.get("NANOLIKA_NODE", "node"), str(Path(__file__).with_suffix(".mjs"))],
        input=json.dumps(data),
        text=True,
        check=True,
    )
