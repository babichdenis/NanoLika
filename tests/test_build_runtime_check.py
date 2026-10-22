"""Exercise the packaging probe with real processes and loopback HTTP servers."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import TCPServer
from types import SimpleNamespace
from typing import Any

import pytest


class LoopbackServer(TCPServer):
    allow_reuse_address = True


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/desktop-app/build-runtime.py"
CHILD = r"""
import os
import sys
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import TCPServer

class LoopbackServer(TCPServer):
    allow_reuse_address = True

Path(os.environ["NANOMUSE_DATA_DIR"], "workspace").mkdir(parents=True)
if sys.argv[1] == "waiting":
    print("waiting forever for startup", flush=True)
    Path(os.environ["NANOMUSE_DATA_DIR"], "fixture-ready").touch()
    time.sleep(60)
else:
    sys.stdout.write("x" * (1024 * 1024))
    sys.stdout.flush()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"ok": true}' if self.path == "/api/health" else b'<div id="root"></div>'
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    port = int(sys.argv[sys.argv.index("--port") + 1])
    # HTTPServer reverse-resolves its address; this loopback fixture needs no DNS.
    LoopbackServer(("127.0.0.1", port), Handler).serve_forever()
"""
Probe = Callable[[str], BaseException | None]


@pytest.fixture
def probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Probe:
    spec = importlib.util.spec_from_file_location("build_runtime_check", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = tmp_path
    child = tmp_path / "runtime.py"
    child.write_text(CHILD, encoding="utf-8")
    processes: list[subprocess.Popen] = []

    def run(mode: str) -> BaseException | None:
        def start(command: list[str], **kwargs: Any) -> subprocess.Popen:
            # Replace only the packaged executable with a portable Python fixture.
            # The process, pipes/files, server, polling and cleanup remain real.
            process = subprocess.Popen([sys.executable, str(child), mode, *command[1:]], **kwargs)
            processes.append(process)
            if mode == "waiting":
                ready = Path(kwargs["env"]["NANOMUSE_DATA_DIR"]) / "fixture-ready"
                deadline = time.monotonic() + 15
                while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.01)
                if not ready.exists():
                    process.kill()
                    process.wait(5)
                    raise AssertionError("the timeout fixture did not start")
            return process

        monkeypatch.setattr(
            module,
            "subprocess",
            SimpleNamespace(
                Popen=start,
                PIPE=subprocess.PIPE,
                STDOUT=subprocess.STDOUT,
                TimeoutExpired=subprocess.TimeoutExpired,
            ),
        )
        # Accelerate deliberate timeout cases, not successful process startup.
        # Slow CI hosts still get the real startup deadline and a separate watchdog.
        clock_rate = 30 if mode == "waiting" else 1
        monkeypatch.setattr(
            module,
            "time",
            SimpleNamespace(
                time=lambda: time.monotonic() * clock_rate,
                monotonic=lambda: time.monotonic() * clock_rate,
                sleep=lambda seconds: time.sleep(seconds / clock_rate),
            ),
        )
        result: list[BaseException] = []

        def invoke() -> None:
            try:
                module.check(child)
            except BaseException as error:
                result.append(error)

        worker = threading.Thread(target=invoke, daemon=True)
        worker.start()
        worker.join(30 if mode == "waiting" else 45)
        hung = worker.is_alive()
        if hung:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait(5)
            worker.join(2)
        assert not hung, "runtime probe exceeded its test watchdog"
        assert all(p.poll() is not None for p in processes), "probe left a child running"
        assert not (tmp_path / "build/runtime-check-home").exists()
        assert not (tmp_path / "build/runtime-check-cwd").exists()
        return result[0] if result else None

    return run


def test_verbose_runtime_can_start_without_blocking_on_logs(probe: Probe) -> None:
    assert probe("chatty") is None


def test_unready_live_runtime_times_out_with_logs_and_stops(probe: Probe) -> None:
    error = probe("waiting")
    assert isinstance(error, SystemExit)
    assert "waiting forever for startup" in str(error)


def test_probe_does_not_accept_an_existing_service_on_8765(probe: Probe) -> None:
    class ExistingService(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = (
                json.dumps({"ok": True}).encode()
                if self.path == "/api/health"
                else b'<div id="root"></div>'
            )
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            pass

    try:
        server = LoopbackServer(("127.0.0.1", 8765), ExistingService)
    except OSError:
        pytest.skip("port 8765 is already in use")
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        error = probe("waiting")
        assert isinstance(error, SystemExit), "unready child was accepted as an existing service"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)


def test_failed_built_runtime_does_not_replace_the_staged_copy(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("runtime_atomic_stage", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "DIST", tmp_path / "dist")
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "old-runtime").write_text("working", encoding="utf-8")

    def build_output(*_args, **_kwargs):
        built = module.DIST / "nanomuse"
        built.mkdir(parents=True)
        (built / ("nanomuse.exe" if sys.platform == "win32" else "nanomuse")).write_text(
            "new", encoding="utf-8"
        )

    monkeypatch.setattr(module, "run", build_output)

    def refuse(_exe):
        raise SystemExit("new runtime failed its health check")

    monkeypatch.setattr(module, "check", refuse)
    with pytest.raises(SystemExit, match="health check"):
        module.build(staged, verify=True)
    assert (staged / "old-runtime").read_text(encoding="utf-8") == "working"
