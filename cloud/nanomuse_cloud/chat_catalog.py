"""Metadata-only chat catalogue and per-client read cursors, alongside the old sync API."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from .service import CloudError
from .sync import _conversation_row, _message_row, valid_id


class ArchiveMark(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    archived: bool


class ReadMark(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    client: str = Field(min_length=4, max_length=80, pattern=r"^[a-zA-Z0-9._:-]+$")
    through_seq: int = Field(ge=0, le=9_007_199_254_740_991)


class ChatCatalog:
    def __init__(self, store: Any):
        self.store, self.db = store, store.db
        with self.db.tx() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS sync_read_marks (
                account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                client TEXT NOT NULL, cid TEXT NOT NULL, seen INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(account_id,client,cid),
                FOREIGN KEY(account_id,cid) REFERENCES sync_conversations(account_id,cid) ON DELETE CASCADE)""")

    def check(self, c: Any, account: str, cid: str = "") -> None:
        if not self.store._enabled(c, account):
            raise CloudError(409, "sync_off", "Conversation sync is off")
        if cid and (
            not valid_id(cid)
            or c.execute(
                "SELECT 1 FROM sync_conversations WHERE account_id=? AND cid=? AND deleted=0",
                (account, cid),
            ).fetchone()
            is None
        ):
            raise CloudError(404, "chat_missing", "This chat is not available")

    def catalog(self, account: str, client: str, device: str) -> dict:
        with self.db.tx() as c:
            self.check(c, account)
            names = self.store._device_names(c, account)
            rows = c.execute(
                """SELECT sc.*,
                COALESCE(MAX(sm.seq),0) last_seq,
                COALESCE(MAX(sm.created_at),sc.updated_at) message_at,
                COUNT(sm.mid) message_count,
                COALESCE(SUM(CASE WHEN sm.seq>COALESCE(r.seen,0) AND NOT (sm.device=? AND sm.role='user')
                    THEN 1 ELSE 0 END),0) unread
                FROM sync_conversations sc
                LEFT JOIN sync_messages sm ON sm.account_id=sc.account_id AND sm.cid=sc.cid AND sm.deleted=0
                LEFT JOIN sync_read_marks r ON r.account_id=sc.account_id AND r.cid=sc.cid AND r.client=?
                WHERE sc.account_id=? AND sc.deleted=0
                GROUP BY sc.cid ORDER BY MAX(sc.updated_at,COALESCE(MAX(sm.created_at),0)) DESC,sc.cid""",
                (device, client, account),
            ).fetchall()
            deleted = [
                _conversation_row(row, names)
                for row in c.execute("SELECT * FROM sync_conversations WHERE account_id=? AND deleted=1", (account,))
            ]
            output = []
            for r in rows:
                item = _conversation_row(r, names)
                item.update(
                    last_seq=int(r["last_seq"]),
                    message_count=int(r["message_count"]),
                    unread=int(r["unread"]),
                    updated_at=max(item["updated_at"], int(r["message_at"])),
                )
                output.append(item)
            return {
                "account_id": account,
                "conversations": output,
                "deleted": deleted,
                "cursor": self.store._cursor(c, account),
                "complete": True,
            }

    def set_archived(self, account: str, cid: str, archived: bool) -> dict:
        return self.store.set_archived(account, cid, archived)

    def messages(self, account: str, cid: str, after: int, before: int, limit: int) -> dict:
        with self.db.tx() as c:
            self.check(c, account, cid)
            snapshot = self.store._cursor(c, account)
            names = self.store._device_names(c, account)
            if after:
                rows = c.execute(
                    "SELECT * FROM sync_messages WHERE account_id=? AND cid=? AND seq>? AND seq<=? ORDER BY seq LIMIT ?",
                    (account, cid, after, snapshot, limit + 1),
                ).fetchall()
                more = len(rows) > limit
                rows = rows[:limit]
                has_older = False
            else:
                ceiling = min(before or snapshot + 1, snapshot + 1)
                rows = c.execute(
                    "SELECT * FROM sync_messages WHERE account_id=? AND cid=? AND seq<? ORDER BY seq DESC LIMIT ?",
                    (account, cid, ceiling, limit + 1),
                ).fetchall()
                has_older = len(rows) > limit
                rows = list(reversed(rows[:limit]))
                more = False
            return {
                "account_id": account,
                "cid": cid,
                "messages": [_message_row(r, names) for r in rows],
                "cursor": int(rows[-1]["seq"]) if more else snapshot,
                "more": more,
                "has_older": has_older,
                "through_seq": snapshot,
            }

    def mark(self, account: str, cid: str, body: ReadMark) -> dict:
        with self.db.tx() as c:
            self.check(c, account, cid)
            if body.through_seq > self.store._cursor(c, account):
                raise CloudError(400, "bad_cursor", "The read cursor is ahead of the chat")
            count = c.execute("SELECT COUNT(DISTINCT client) FROM sync_read_marks WHERE account_id=?", (account,)).fetchone()[0]
            exists = c.execute("SELECT 1 FROM sync_read_marks WHERE account_id=? AND client=?", (account, body.client)).fetchone()
            if not exists and count >= 128:
                raise CloudError(409, "client_limit", "Too many chat readers")
            c.execute(
                "INSERT INTO sync_read_marks(account_id,client,cid,seen) VALUES(?,?,?,?) "
                "ON CONFLICT(account_id,client,cid) DO UPDATE SET seen=MAX(seen,excluded.seen)",
                (account, body.client, cid, body.through_seq),
            )
            seen = c.execute(
                "SELECT seen FROM sync_read_marks WHERE account_id=? AND client=? AND cid=?", (account, body.client, cid)
            ).fetchone()[0]
            return {"cid": cid, "seen": int(seen)}


def install(app: Any, store: Any, caller_dep: Any) -> None:
    service = ChatCatalog(store)

    @app.get("/v1/sync/catalog")
    async def catalog(
        response: Response,
        client: str = Query(min_length=4, max_length=80, pattern=r"^[a-zA-Z0-9._:-]+$"),
        device: str = Query(default="", max_length=80),
        caller: Any = Depends(caller_dep),
    ) -> dict:  # noqa: B008
        response.headers["Cache-Control"] = "no-store"
        return service.catalog(caller.account_id, client, device)

    @app.post("/v1/sync/conversations/{cid}/archive")
    async def archive(cid: str, body: ArchiveMark, response: Response, caller: Any = Depends(caller_dep)) -> dict:
        response.headers["Cache-Control"] = "no-store"
        result = service.set_archived(caller.account_id, cid, body.archived)
        hub = getattr(app.state, "hub", None)
        if hub is not None:
            await hub.notify_sync(caller.account_id, result["cursor"], "")
        return result

    @app.get("/v1/sync/conversations/{cid}/messages")
    async def messages(
        cid: str,
        response: Response,
        after: int = Query(default=0, ge=0, le=9_007_199_254_740_991),
        before: int = Query(default=0, ge=0, le=9_007_199_254_740_991),
        limit: int = Query(default=300, ge=1, le=500),
        caller: Any = Depends(caller_dep),
    ) -> dict:  # noqa: B008
        if after and before:
            raise CloudError(400, "bad_cursor", "Choose after or before")
        response.headers["Cache-Control"] = "no-store"
        return service.messages(caller.account_id, cid, after, before, limit)

    @app.post("/v1/sync/conversations/{cid}/read")
    async def read(cid: str, body: ReadMark, response: Response, caller: Any = Depends(caller_dep)) -> dict:  # noqa: B008
        response.headers["Cache-Control"] = "no-store"
        return service.mark(caller.account_id, cid, body)
