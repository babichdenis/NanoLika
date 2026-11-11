"""The cloud worker: HTTP in, the right account's MuseService out.

The relay has already proved who is calling. It sends the account id and the account's
Cloud key in two headers of an internal request; this worker builds (or reuses) that
account's service through :class:`AccountRuntime` and hands the request to the service's
own FastAPI app. The browser never sees the runtime token: the worker adds it here.

Run: ``python -m nanomuse.server.cloud_worker`` (uvicorn on 127.0.0.1:8789).
"""

from __future__ import annotations

import asyncio
import hmac
import os
import uuid
import weakref
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

import httpx
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers

from nanomuse import timeutil
from nanomuse.config import load_settings
from nanomuse.logger import logger
from nanomuse.server.accounts import AccountRuntime, CapacityError
from nanomuse.server.api import _handle_ws_message, create_app

DROP_REQUEST = {
    "host",
    "content-length",
    "connection",
    "keep-alive",
    "authorization",
    "cookie",
    "accept-encoding",
    "x-account",
    "x-cloud-key",
    "x-worker-secret",
}
DROP_RESPONSE = {"content-length", "content-encoding", "transfer-encoding", "connection"}

#: The relay's own secret, when the deployment sets one: the worker trusts X-Account and
#: X-Cloud-Key only from a caller that also carries it. Empty keeps the loopback-only
#: default of the pilot, and the worker then relies on the network for that trust.
WORKER_SECRET = os.environ.get("NANOMUSE_WORKER_SECRET", "").strip()


def worker_allows(headers: Mapping[str, str], secret: str | None = None) -> bool:
    """Whether a request may act for an account: the shared secret, byte for byte."""
    wanted = WORKER_SECRET if secret is None else secret
    if not wanted:
        return True
    return hmac.compare_digest(str(headers.get("x-worker-secret", "")), wanted)


class RuntimeAccountContext:
    """Carry the relay-proved owner through HTTP, WebSocket and their child tasks.

    Authentication is still enforced by the individual endpoints. This wrapper uses
    exactly the same worker secret check and never treats a denied header as an owner.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        account = str(headers.get("x-account", "") or "").strip()
        # Clear any inherited context for a denied/anonymous request. Endpoints still
        # perform their own authorization; this wrapper grants no access on its own.
        verified = account if account and worker_allows(headers) else ""
        with timeutil.account_runtime(verified):
            await self.app(scope, receive, send)


class AppCache:
    """One FastAPI app per live service.

    The service is rebuilt after a stop, and an app keeps the service it was made for: a
    cache keyed by the account alone would hand HTTP the old service while WebSocket had
    the new one. The cache holds the service weakly and compares identity, so a rebuilt
    service always gets a rebuilt app and a dead one leaves nothing behind.
    """

    def __init__(self, make: Callable[[Any], FastAPI]) -> None:
        self._make = make
        self._apps: dict[str, tuple[Any, FastAPI]] = {}

    def app_for(self, account_id: str, service: Any) -> FastAPI:  # noqa: ANN401
        held = self._apps.get(account_id)
        if held is not None and held[0]() is service:
            return held[1]
        made = self._make(service)
        self._apps[account_id] = (weakref.ref(service), made)
        return made

    def drop(self, account_id: str) -> None:
        """The service is gone: its app held a reference to it, so let both go."""
        self._apps.pop(account_id, None)


async def _models(service: Any) -> JSONResponse:  # noqa: ANN401
    """The account's models for the chat picker: the relay's menu and the current choice."""
    llm = service.settings.llm
    # `/v1/me/models` carries the account's shelf with prices; `/v1/models` does not.
    menu_rows: list[Any] = []
    try:
        shelf = await service.hub.cloud.me_models()
        if isinstance(shelf, dict) and isinstance(shelf.get("models"), list):
            menu_rows = shelf["models"]
    except Exception:  # noqa: BLE001 — an older relay or a cold client falls back to the plain menu
        menu_rows = []
    if not menu_rows:
        menu_rows = list(getattr(service.hub, "models", []) or [])
    rows = []
    for row in menu_rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        nano = row.get("nanomuse")
        if not isinstance(nano, dict):
            nano = {}
        reason = (
            row.get("reasoning")
            if isinstance(row.get("reasoning"), dict)
            else nano.get("reasoning")
        )
        rows.append(
            {
                "id": str(row["id"]),
                "name": str(row.get("name") or row["id"]),
                "vendor": str(row.get("vendor") or ""),
                "kind": str(row.get("kind") or nano.get("kind") or ""),
                "free": bool(row.get("free") or nano.get("free")),
                # the relay's own plan gate: the model is in the menu but this account's
                # tariff does not open it (viewable, not runnable)
                "locked": bool(row.get("plan_locked") or nano.get("plan_locked")),
                "reasoning": reason if isinstance(reason, dict) else None,
                "sell": row.get("sell") if isinstance(row.get("sell"), dict) else None,
                "context": row.get("context")
                if isinstance(row.get("context"), int)
                else nano.get("context"),
            }
        )
    return JSONResponse(
        {
            "current": {
                "provider": llm.provider,
                "model": llm.model,
                "cloud": bool(getattr(llm, "cloud", False)),
            },
            "models": rows,
        }
    )


def build_worker() -> FastAPI:
    settings = load_settings()
    # The worker has no config file of its own; the relay is ours, not the upstream one the
    # package ships as its default.
    settings.cloud.base_url = os.environ.get(
        "NANOMUSE_CLOUD_URL", "https://cloud.nanolika.ru"
    ).rstrip("/")
    root = Path(os.environ.get("NANOMUSE_ACCOUNTS_DIR", str(settings.data_dir / "accounts")))
    runtime = AccountRuntime(
        settings,
        root,
        limit=int(os.environ.get("NANOMUSE_ACCOUNTS_LIMIT", "24")),
        idle_seconds=float(os.environ.get("NANOMUSE_ACCOUNTS_IDLE_S", "900")),
        allow_code=os.environ.get("NANOMUSE_WORKER_ALLOW_CODE", "0").lower()
        in ("1", "true", "yes"),
    )
    apps = AppCache(lambda service: create_app(service.settings, service=service))
    runtime.on_stop(apps.drop)
    app = FastAPI(title="NanoLika cloud worker", docs_url=None, redoc_url=None)
    app.add_middleware(RuntimeAccountContext)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"ok": True, "active": runtime.active()}

    @app.get("/api/models")
    async def account_models(request: Request) -> JSONResponse:
        """The account's models for the chat picker: the relay's menu and the current choice."""
        if not worker_allows(request.headers):
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        account_id = request.headers.get("x-account", "").strip()
        cloud_key = request.headers.get("x-cloud-key", "").strip()
        if not account_id:
            return JSONResponse({"detail": "no account"}, status_code=401)
        try:
            service = await runtime.service(account_id, cloud_key)
        except CapacityError:
            return JSONResponse({"detail": "the runtime is busy"}, status_code=503)
        except Exception as exc:  # noqa: BLE001
            logger.warning("worker: models account {} did not start: {}", account_id[:8], exc)
            return JSONResponse({"detail": "runtime unavailable"}, status_code=502)
        runtime.begin(account_id)
        try:
            return await _models(service)
        finally:
            runtime.end(account_id)

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    async def proxy(path: str, request: Request) -> Response:
        if not worker_allows(request.headers):
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        account_id = request.headers.get("x-account", "").strip()
        cloud_key = request.headers.get("x-cloud-key", "").strip()
        if not account_id:
            return JSONResponse({"detail": "no account"}, status_code=401)
        try:
            service = await runtime.service(account_id, cloud_key)
        except CapacityError:
            return JSONResponse({"detail": "the runtime is busy"}, status_code=503)
        except Exception as exc:  # noqa: BLE001 — an account that cannot start is a plain 502
            logger.warning("worker: account {} did not start: {}", account_id[:8], exc)
            return JSONResponse({"detail": "runtime unavailable"}, status_code=502)
        headers = {
            key: value for key, value in request.headers.items() if key.lower() not in DROP_REQUEST
        }
        headers["authorization"] = "Bearer " + runtime.token(account_id)
        target = "/" + path
        if request.url.query:
            target += "?" + request.url.query
        transport = httpx.ASGITransport(app=apps.app_for(account_id, service))
        runtime.begin(account_id)
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://worker", timeout=300.0
            ) as client:
                answer = await client.request(
                    request.method,
                    target,
                    headers=headers,
                    content=await request.body(),
                )
        except Exception as exc:  # noqa: BLE001 — the service's own failure is a 502, not a crash
            logger.warning("worker: account {} request failed: {}", account_id[:8], exc)
            return JSONResponse({"detail": "runtime error"}, status_code=502)
        finally:
            runtime.end(account_id)
        return Response(
            content=answer.content,
            status_code=answer.status_code,
            headers={
                key: value
                for key, value in answer.headers.items()
                if key.lower() not in DROP_RESPONSE
            },
            media_type=answer.headers.get("content-type"),
        )

    @app.websocket("/ws")
    async def ws_proxy(ws: WebSocket) -> None:
        """The account's live events: the relay has already proved the session."""
        if not worker_allows(ws.headers):
            await ws.close(code=4403)
            return
        account_id = ws.headers.get("x-account", "").strip()
        cloud_key = ws.headers.get("x-cloud-key", "").strip()
        if not account_id:
            await ws.close(code=4401)
            return
        try:
            service = cast(Any, await runtime.service(account_id, cloud_key))
        except CapacityError:
            await ws.close(code=4503)
            return
        except Exception as exc:  # noqa: BLE001 — no runtime, a plain close
            logger.warning("worker: ws account {} did not start: {}", account_id[:8], exc)
            await ws.close(code=4502)
            return
        runtime.begin(account_id)
        conn_id = f"ws-{uuid.uuid4().hex[:8]}"
        lock = asyncio.Lock()
        queue = None
        pump_task = None
        beat_task = None
        accepted = False

        async def send(message: dict[str, Any]) -> None:
            async with lock:
                await ws.send_json(message)

        async def keep_warm() -> None:
            # A chat can sit silent for an hour; the sweep counts a live socket as work.
            while True:
                await asyncio.sleep(60)
                runtime.touch(account_id)

        try:
            # The service must be protected even while the socket handshake yields.
            await ws.accept()
            accepted = True
            queue = service.bus.subscribe()
            await send({"kind": "hello", "state": service.state()})

            async def pump() -> None:
                assert queue is not None
                while True:
                    await send(await queue.get())

            pump_task = asyncio.create_task(pump())
            beat_task = asyncio.create_task(keep_warm())
            while True:
                data = await ws.receive_json()
                # The client's own auth frame names a runtime token it does not have here;
                # the relay already proved the session, so the frame is ignored.
                if isinstance(data, dict) and data.get("kind") == "auth":
                    continue
                await _handle_ws_message(service, ws, data, conn_id=conn_id, send=send)
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("worker ws closed: {}", exc)
        finally:
            for task in (pump_task, beat_task):
                if task is not None:
                    task.cancel()
            if queue is not None:
                service.bus.unsubscribe(queue)
            if accepted:
                service.phone.detach(conn_id)
            runtime.end(account_id)

    @app.on_event("startup")
    async def startup() -> None:
        async def sweeper() -> None:
            while True:
                await asyncio.sleep(60)
                try:
                    await runtime.sweep()
                    # A reminder belongs to the account, not to a warm process: an evicted
                    # account is raised again when its time comes.
                    await runtime.wake_due()
                except Exception as exc:  # noqa: BLE001 — the sweep must not kill the worker
                    logger.warning("worker: sweep failed: {}", exc)

        app.state.sweeper = asyncio.create_task(sweeper())

    @app.on_event("shutdown")
    async def shutdown() -> None:
        task = getattr(app.state, "sweeper", None)
        if task is not None:
            task.cancel()
        await runtime.close()

    return app


def main() -> None:
    import uvicorn

    host = os.environ.get("NANOMUSE_WORKER_HOST", "127.0.0.1")
    port = int(os.environ.get("NANOMUSE_WORKER_PORT", "8789"))
    uvicorn.run(build_worker(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
