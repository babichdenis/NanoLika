"""The cloud worker: HTTP in, the right account's MuseService out.

The relay has already proved who is calling. It sends the account id and the account's
Cloud key in two headers of an internal request; this worker builds (or reuses) that
account's service through :class:`AccountRuntime` and hands the request to the service's
own FastAPI app. The browser never sees the runtime token: the worker adds it here.

Run: ``python -m nanomuse.server.cloud_worker`` (uvicorn on 127.0.0.1:8789).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from nanomuse.config import load_settings
from nanomuse.logger import logger
from nanomuse.server.accounts import AccountRuntime
from nanomuse.server.api import _handle_ws_message, create_app

DROP_REQUEST = {"host", "content-length", "connection", "keep-alive", "authorization", "cookie", "accept-encoding", "x-account", "x-cloud-key"}
DROP_RESPONSE = {"content-length", "content-encoding", "transfer-encoding", "connection"}


def build_worker() -> FastAPI:
    settings = load_settings()
    root = Path(os.environ.get("NANOMUSE_ACCOUNTS_DIR", str(settings.data_dir / "accounts")))
    runtime = AccountRuntime(
        settings,
        root,
        limit=int(os.environ.get("NANOMUSE_ACCOUNTS_LIMIT", "24")),
        idle_seconds=float(os.environ.get("NANOMUSE_ACCOUNTS_IDLE_S", "900")),
        allow_code=os.environ.get("NANOMUSE_WORKER_ALLOW_CODE", "0").lower() in ("1", "true", "yes"),
    )
    apps: dict[str, FastAPI] = {}
    app = FastAPI(title="NanoLika cloud worker", docs_url=None, redoc_url=None)

    def sub_app(account_id: str, service: Any) -> FastAPI:
        made = apps.get(account_id)
        if made is None:
            made = create_app(service.settings, service=service)
            apps[account_id] = made
        return made

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"ok": True, "active": runtime.active()}

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    async def proxy(path: str, request: Request) -> Response:
        account_id = request.headers.get("x-account", "").strip()
        cloud_key = request.headers.get("x-cloud-key", "").strip()
        if not account_id:
            return JSONResponse({"detail": "no account"}, status_code=401)
        try:
            service = await runtime.service(account_id, cloud_key)
        except Exception as exc:  # noqa: BLE001 — an account that cannot start is a plain 502
            logger.warning("worker: account {} did not start: {}", account_id[:8], exc)
            return JSONResponse({"detail": "runtime unavailable"}, status_code=502)
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in DROP_REQUEST
        }
        headers["authorization"] = "Bearer " + runtime.token(account_id)
        target = "/" + path
        if request.url.query:
            target += "?" + request.url.query
        transport = httpx.ASGITransport(app=sub_app(account_id, service))
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://worker", timeout=300.0) as client:
                answer = await client.request(
                    request.method,
                    target,
                    headers=headers,
                    content=await request.body(),
                )
        except Exception as exc:  # noqa: BLE001 — the service's own failure is a 502, not a crash
            logger.warning("worker: account {} request failed: {}", account_id[:8], exc)
            return JSONResponse({"detail": "runtime error"}, status_code=502)
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
        account_id = ws.headers.get("x-account", "").strip()
        cloud_key = ws.headers.get("x-cloud-key", "").strip()
        if not account_id:
            await ws.close(code=4401)
            return
        try:
            service = await runtime.service(account_id, cloud_key)
        except Exception as exc:  # noqa: BLE001 — no runtime, a plain close
            logger.warning("worker: ws account {} did not start: {}", account_id[:8], exc)
            await ws.close(code=4502)
            return
        await ws.accept()
        conn_id = f"ws-{uuid.uuid4().hex[:8]}"
        lock = asyncio.Lock()

        async def send(message: dict[str, Any]) -> None:
            async with lock:
                await ws.send_json(message)

        queue = service.bus.subscribe()
        await send({"kind": "hello", "state": service.state()})

        async def pump() -> None:
            while True:
                await send(await queue.get())

        pump_task = asyncio.create_task(pump())
        try:
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
            pump_task.cancel()
            service.bus.unsubscribe(queue)
            service.phone.detach(conn_id)

    @app.on_event("startup")
    async def startup() -> None:
        async def sweeper() -> None:
            while True:
                await asyncio.sleep(60)
                try:
                    await runtime.sweep()
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
