"""Pull shared chat metadata globally, and message bodies only for selected chats."""

from __future__ import annotations

from typing import Any

from nanomuse.cloud import CloudError


class LazyCatalog:
    def __init__(self, engine: Any):
        self.engine = engine
        self.selected: dict[str, str] = {}
        self.rows: dict[str, dict] = {}
        self.unread: dict[str, int] = {}
        self.supported: bool | None = None
        self.quiet_threads: set[str] = set()
        self.account = engine.account_id

    def select(self, reader: str, thread: str) -> None:
        self.selected[reader] = thread

    async def open(self, thread: str, reader: str = "local-web") -> None:
        self.select(reader, thread)
        self.quiet_threads.add(thread)
        try:
            await self.engine.pull()
        finally:
            self.quiet_threads.discard(thread)

    async def pull(self) -> int | None:
        e = self.engine
        account = e.account_id
        if self.account != e.account_id:
            self.account = e.account_id
            self.rows.clear()
            self.unread.clear()
            e.state["message_cursors"] = {}
        if self.supported is False or not hasattr(e.client, "catalog"):
            return None
        try:
            out = await e.client.catalog(e.svc.hub.device_id, e.svc.hub.device_id)
        except CloudError as exc:
            if exc.status == 404:
                self.supported = False
                return None
            raise
        if e.account_id != account or (out.get("account_id") and out["account_id"] != account):
            return 0
        self.supported = True
        e.state["catalog_mode"] = True
        rows = {r["cid"]: r for r in out.get("conversations", []) if r.get("cid")}
        changed = rows != self.rows
        for row in out.get("deleted", []):
            e._apply_conversation(row)
        self.rows = rows
        self.unread = {r["cid"]: int(r.get("unread") or 0) for r in rows.values()}
        e._applying = True
        try:
            for row in rows.values():
                e._apply_conversation(row)
        finally:
            e._applying = False
        selected = set(self.selected.values())
        picker = getattr(getattr(e.svc, "channels", None), "picker", None)
        if picker is not None:
            selected.update(picker.active_threads())
        cursors = e.state.setdefault("message_cursors", {})
        applied = 0
        for tid in selected:
            thread = e.svc.threads.get(tid)
            if thread is None or not e._mine(thread):
                continue
            cid = e.cid_of(tid)
            if cid not in rows:
                continue
            for _ in range(50):
                after = int(cursors.get(cid) or 0)
                if e.account_id != account:
                    return 0
                page = await e.client.messages(cid, after)
                if e.account_id != account or (
                    page.get("account_id") and page["account_id"] != account
                ):
                    return 0
                e._applying = True
                try:
                    for row in page.get("messages", []):
                        if not after or tid in self.quiet_threads:
                            row = {**row, "quiet": True}
                        e._apply_message(row)
                        applied += 1
                finally:
                    e._applying = False
                cursors[cid] = max(int(cursors.get(cid) or 0), int(page.get("cursor") or 0))
                if not page.get("more"):
                    await e.client.read(cid, e.svc.hub.device_id, int(page.get("through_seq") or 0))
                    self.unread[cid] = 0
                    break
        if e.account_id != account:
            return 0
        e.state["cursor"] = int(out.get("cursor") or 0)
        e._save()
        if changed or applied:
            e.svc.bus.publish({"kind": "sync", "sync": e.view()})
        return applied
