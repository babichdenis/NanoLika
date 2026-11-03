"""The entry point of the bundled runtime (PyInstaller): ``nanomuse`` with all its
subcommands, so the desktop app can run ``nanomuse serve`` without a Python install.
"""

from __future__ import annotations

import multiprocessing
import sys


def _utf8_streams() -> None:
    """Windows pipes get the console code page, and a ✓ in a message kills the run.

    The app spawns this runtime with pipes, so stdout/stderr must be UTF-8 (with
    replacement, never a crash) regardless of the system locale.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main() -> None:
    _utf8_streams()
    multiprocessing.freeze_support()
    # the bundle's OpenSSL knows the build machine's certificate paths, not this one's
    from nanomuse.certs import ensure_ca_bundle

    ensure_ca_bundle()
    # the bridge commands (console scripts in a pip install) are subcommands of this one
    # executable: `nanomuse device …`, `nanomuse browser …`, `nanomuse open …`
    if len(sys.argv) > 1 and sys.argv[1] in ("device", "browser", "open"):
        from nanomuse.bridge import cli as bridge

        which = sys.argv.pop(1)
        {"device": bridge.device_main, "browser": bridge.browser_main, "open": bridge.open_main}[
            which
        ]()
        return
    from nanomuse.cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()
