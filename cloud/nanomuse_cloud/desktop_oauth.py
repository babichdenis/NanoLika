"""Short-lived, PKCE-bound browser handoff to the native desktop client.

The browser receives only a one-use code. Account keys are returned over HTTPS
to the client that holds its verifier, never through a custom URI scheme.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from collections.abc import Callable
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .db import Database
from .identifiers import parse
from .service import Cloud, CloudError

CODE_TTL_S = 120
OPAQUE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
VERIFIER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")


def desktop_request(callback: str, challenge: str, state: str) -> dict[str, str] | None:
    """Allow only a literal IPv4 loopback listener and a fixed callback path."""
    if not any((callback, challenge, state)):
        return None
    try:
        url = urlsplit(callback)
        valid = (
            url.scheme == "http"
            and url.hostname == "127.0.0.1"
            and url.port is not None
            and 1024 <= url.port <= 65535
            and url.netloc == f"127.0.0.1:{url.port}"
            and url.path == "/nanolika-signin"
            and not url.query
            and not url.fragment
            and OPAQUE.fullmatch(challenge)
            and OPAQUE.fullmatch(state)
        )
    except ValueError:
        valid = False
    if not valid:
        raise CloudError(400, "bad_request", "Invalid desktop sign-in request")
    return {"callback": callback, "challenge": challenge, "state": state}


def desktop_redirect(desktop: dict[str, str], **answer: str) -> RedirectResponse:
    # Revalidate signed cookie data too, so there is a single redirect policy.
    desktop_request(desktop["callback"], desktop["challenge"], desktop["state"])
    return RedirectResponse(
        desktop["callback"] + "?" + urlencode({"state": desktop["state"], **answer}),
        status_code=302,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


class DesktopCodes:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self.db = db
        self.clock = clock
        with db.tx() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS desktop_oauth_codes ("
                "hash TEXT PRIMARY KEY, challenge TEXT NOT NULL, email TEXT NOT NULL, "
                "provider TEXT NOT NULL, expires INTEGER NOT NULL)"
            )

    def issue(self, email: str, provider: str, challenge: str) -> str:
        code = secrets.token_urlsafe(32)
        with self.db.tx() as connection:
            connection.execute("DELETE FROM desktop_oauth_codes WHERE expires<=?", (int(self.clock()),))
            connection.execute(
                "INSERT INTO desktop_oauth_codes VALUES (?,?,?,?,?)",
                (hashlib.sha256(code.encode()).hexdigest(), challenge, email, provider, int(self.clock()) + CODE_TTL_S),
            )
        return code

    def consume(self, code: str, verifier: str) -> tuple[str, str]:
        if not OPAQUE.fullmatch(code) or not VERIFIER.fullmatch(verifier):
            raise CloudError(400, "bad_request", "Invalid desktop sign-in proof")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        with self.db.tx() as connection:
            row = connection.execute("SELECT * FROM desktop_oauth_codes WHERE hash=?", (code_hash,)).fetchone()
            if row is None or row["expires"] <= self.clock() or not hmac.compare_digest(row["challenge"], challenge):
                raise CloudError(400, "bad_request", "Desktop sign-in expired or its proof is invalid")
            connection.execute("DELETE FROM desktop_oauth_codes WHERE hash=?", (code_hash,))
            return row["email"], row["provider"]


def install_desktop_exchange(app: FastAPI, cloud: Cloud) -> DesktopCodes:
    codes = DesktopCodes(cloud.db)

    @app.post("/v1/auth/desktop/exchange")
    async def desktop_exchange(request: Request) -> JSONResponse:
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 4096:
                raise CloudError(413, "bad_request", "Sign-in request is too large")
        try:
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError()
        except (ValueError, TypeError) as exc:
            raise CloudError(400, "bad_request", "Expected a sign-in request") from exc
        email, provider = await asyncio.to_thread(codes.consume, str(body.get("code", "")), str(body.get("verifier", "")))
        key, caller, created = await asyncio.to_thread(
            cloud.sign_in,
            parse(email),
            str(body.get("device", "NanoLika Desktop"))[:80],
            f"oauth.{provider}",
        )
        return JSONResponse(
            {"api_key": key, "created": created, **cloud.me(caller)},
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    return codes
