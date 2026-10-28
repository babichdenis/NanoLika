"""Opaque website cookies, limited by the sign-in that created them."""

from __future__ import annotations

import hashlib
import secrets
import time

from .service import Caller, Cloud, CloudError

PREFIX = "nmb_"
MAX_AGE_S = 30 * 86400


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class BrowserSessionStore:
    def __init__(self, cloud: Cloud):
        self.cloud = cloud
        self.clock = time.time
        with cloud.db.tx() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS browser_sessions ("
                "token_hash TEXT PRIMARY KEY, key_hash TEXT NOT NULL "
                "REFERENCES api_keys(key_hash) ON DELETE CASCADE, "
                "created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL)"
            )
            connection.execute("CREATE INDEX IF NOT EXISTS browser_sessions_key ON browser_sessions(key_hash)")
            connection.execute("CREATE TABLE IF NOT EXISTS browser_session_meta (name TEXT PRIMARY KEY, value INTEGER NOT NULL)")
            connection.execute(
                "INSERT OR IGNORE INTO browser_session_meta(name,value) VALUES ('migration_start',?)",
                (int(self.clock()),),
            )
            self.migration_start = int(
                connection.execute("SELECT value FROM browser_session_meta WHERE name='migration_start'").fetchone()[0]
            )
        self.legacy_until = self.migration_start + MAX_AGE_S

    def issue(self, caller: Caller, *, legacy: bool = False) -> tuple[str, int]:
        now = int(self.clock())
        parent = self.cloud.db.key(caller.key_hash)
        if parent is None or self.cloud._caller(caller.key_hash) is None:
            raise CloudError(401, "bad_key", "Sign in again in the app")
        expires = now + MAX_AGE_S
        if parent["expires_at"] is not None:
            expires = min(expires, int(parent["expires_at"]))
        if legacy:
            expires = min(expires, self.legacy_until)
        if expires <= now:
            raise CloudError(401, "bad_key", "Sign in again in the app")
        token = PREFIX + secrets.token_urlsafe(32)
        with self.cloud.db.tx() as connection:
            connection.execute("DELETE FROM browser_sessions WHERE expires_at<=?", (now,))
            connection.execute(
                "INSERT INTO browser_sessions(token_hash,key_hash,created_at,expires_at) VALUES (?,?,?,?)",
                (digest(token), caller.key_hash, now, expires),
            )
        return token, expires - now

    def caller(self, token: str) -> Caller | None:
        if not token.startswith(PREFIX) or len(token) > 128:
            return None
        with self.cloud.db.tx() as connection:
            row = connection.execute("SELECT key_hash,expires_at FROM browser_sessions WHERE token_hash=?", (digest(token),)).fetchone()
        if row is None:
            return None
        caller = self.cloud._caller(row["key_hash"]) if row["expires_at"] > int(self.clock()) else None
        if caller is None:
            self.revoke(token)
        return caller

    def legacy_caller(self, token: str) -> Caller | None:
        if int(self.clock()) >= self.legacy_until or len(token) > 256:
            return None
        try:
            caller = self.cloud.authenticate(token)
        except CloudError as error:
            if error.status == 401:
                return None
            raise
        # Only credentials already present when this database first enabled A3 qualify.
        if caller.key_created_at >= self.migration_start:
            return None
        return caller

    def revoke(self, token: str) -> None:
        if token.startswith(PREFIX) and len(token) <= 128:
            with self.cloud.db.tx() as connection:
                connection.execute("DELETE FROM browser_sessions WHERE token_hash=?", (digest(token),))


def browser_store(cloud: Cloud) -> BrowserSessionStore:
    store = getattr(cloud, "_browser_session_store", None)
    if store is None:
        store = BrowserSessionStore(cloud)
        cloud._browser_session_store = store
    return store
