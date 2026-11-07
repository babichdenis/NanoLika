"""The relay's side of the worker trust: one shared secret, sent with every call."""

from __future__ import annotations

from nanomuse_cloud import web_runtime


def test_the_worker_secret_is_sent_only_when_set(monkeypatch):
    monkeypatch.setattr(web_runtime, "WORKER_SECRET", "")
    assert web_runtime._worker_auth() == {}
    monkeypatch.setattr(web_runtime, "WORKER_SECRET", "s3cret")
    assert web_runtime._worker_auth() == {"X-Worker-Secret": "s3cret"}


def test_the_browser_cannot_smuggle_the_worker_header():
    assert "x-worker-secret" in web_runtime.DROP_REQUEST
