"""Pull shared chat metadata globally, and message bodies only for selected chats."""

from __future__ import annotations

import time
from typing import Any

from nanomuse.cloud import CloudError

# One catalogue pass may serve several chats; a long history is finished over several
# passes instead of being cut at an arbitrary page count.
CATALOG_BUDGET_S = 2.0
CATALOG_MAX_APPLIED = 1500
# How many messages one page of the per-chat endpoint carries.
CATALOG_PAGE = 500


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
            e.state["conversation_marks"] = {}
            e.state["catalog_mode"] = False
            e.state["pending_cids"] = []
            e.state["history_before"] = {}
            self.selected.clear()
        if not hasattr(e.client, "catalog"):
            if e.state.get("catalog_mode") or e.state.get("pending_cids"):
                e.state["catalog_mode"] = False
                e.state["pending_cids"] = []
                e._save()
            return None
        if self.supported is False:
            return None
        try:
            out = await e.client.catalog(e.svc.hub.device_id, e.svc.hub.device_id)
        except CloudError as exc:
            if exc.status == 404:
                self.supported = False
                e.state["catalog_mode"] = False
                e.state["pending_cids"] = []
                e._save()
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
        e.state["conversation_marks"] = {
            cid: {"archived": bool(row.get("archived")), "deleted": bool(row.get("deleted"))}
            for cid, row in rows.items()
        }
        for row in out.get("deleted", []):
            e.state["conversation_marks"][row["cid"]] = {"deleted": True, "archived": True}
        self.rows = rows
        self.unread = {r["cid"]: int(r.get("unread") or 0) for r in rows.values()}
        e._applying = True
        try:
            for row in rows.values():
                e._apply_conversation(row)
        finally:
            e._applying = False
        cursors = e.state.setdefault("message_cursors", {})
        # A chat leaves the catalogue (deleted, archived away, another account): its
        # per-chat cursor goes with it instead of growing the state file forever.
        history = e.state.setdefault("history_before", {})
        for stale in [cid for cid in cursors if cid not in rows]:
            cursors.pop(stale, None)
        for stale in [cid for cid in history if cid not in rows]:
            history.pop(stale, None)
        selected = set(self.selected.values())
        picker = getattr(getattr(e.svc, "channels", None), "picker", None)
        if picker is not None:
            selected.update(picker.active_threads())
        by_cid = {str(cid): str(tid) for tid, cid in e.state["cids"].items()}
        pending = {str(cid) for cid in e.state.get("pending_cids", []) if isinstance(cid, str)}
        pending = {cid for cid in pending if cid in rows and cid in by_cid}
        threads = set(selected)
        threads.update(by_cid[cid] for cid in pending if cid in by_cid)
        deadline = time.monotonic() + CATALOG_BUDGET_S
        applied = 0
        for tid in threads:
            thread = e.svc.threads.get(tid)
            if thread is None or not e._mine(thread):
                cid = e.cid_of(tid)
                if cid:
                    pending.discard(cid)
                    history.pop(cid, None)
                continue
            cid = e.cid_of(tid)
            if cid not in rows:
                pending.discard(cid)
                history.pop(cid, None)
                continue
            if cid in pending and time.monotonic() >= deadline:
                continue
            mode = "older" if int(history.get(cid) or 0) else (
                "after" if int(cursors.get(cid) or 0) else "newest"
            )
            while True:
                if applied >= CATALOG_MAX_APPLIED or time.monotonic() >= deadline:
                    pending.add(cid)
                    break
                if e.account_id != account:
                    return 0
                if mode == "older":
                    before = int(history.get(cid) or 0)
                    page = await e.client.messages(cid, before=before, limit=CATALOG_PAGE)
                else:
                    after = int(cursors.get(cid) or 0)
                    page = await e.client.messages(
                        cid, after=0 if mode == "newest" else after, limit=CATALOG_PAGE
                    )
                if e.account_id != account or (
                    page.get("account_id") and page["account_id"] != account
                ):
                    return 0
                current = [row for row in page.get("messages", []) if isinstance(row, dict)]
                quiet = tid in self.quiet_threads or mode != "after"
                e._applying = True
                try:
                    for row in current:
                        if quiet:
                            row = {**row, "quiet": True}
                        e._apply_message(row)
                        applied += 1
                finally:
                    e._applying = False
                if mode == "older":
                    oldest = min((int(row.get("seq") or 0) for row in current), default=0)
                    if page.get("has_older") and oldest > 0:
                        history[cid] = oldest
                        pending.add(cid)
                        continue
                    history.pop(cid, None)
                    # The older part is here; now catch up on anything newer than the
                    # cursor that arrived while the history was being walked.
                    await e.client.read(
                        cid, e.svc.hub.device_id, int(page.get("through_seq") or 0)
                    )
                    self.unread[cid] = 0
                    pending.discard(cid)
                    mode = "after"
                    continue
                if mode == "newest":
                    through = int(page.get("through_seq") or 0)
                    cursors[cid] = through
                    await e.client.read(cid, e.svc.hub.device_id, through)
                    self.unread[cid] = 0
                    oldest = min((int(row.get("seq") or 0) for row in current), default=0)
                    if page.get("has_older") and oldest > 0:
                        history[cid] = oldest
                        pending.add(cid)
                        mode = "older"
                        continue
                    pending.discard(cid)
                    break
                # after: forward pages from the cursor
                next_cursor = max(after, int(page.get("cursor") or 0))
                cursors[cid] = next_cursor
                if not page.get("more"):
                    await e.client.read(
                        cid, e.svc.hub.device_id, int(page.get("through_seq") or 0)
                    )
                    self.unread[cid] = 0
                    pending.discard(cid)
                    break
                if next_cursor <= after:
                    # A page that says "more" without moving forward would spin forever.
                    pending.add(cid)
                    break
        if e.account_id != account:
            return 0
        e._advance_cursor(out.get("cursor"))
        e.state["pending_cids"] = sorted(pending)
        e._save()
        if changed:
            e.svc.bus.publish({"kind": "threads", "threads": e.list_rows()})
        if changed or applied:
            e.svc.bus.publish({"kind": "sync", "sync": e.view()})
        return applied
