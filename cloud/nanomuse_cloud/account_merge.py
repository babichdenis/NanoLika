"""Join the account a chat made to the account its person already uses.

The relay can end up with two accounts for one person: one from the app (an address, the
devices, the history), one the bot made when the chat wrote first. A short code is not
enough to join them, and `link_channel` says so (`link_taken`): two accounts carry two
allowances, two histories and two sets of sessions, and a quiet merge on a code is how a
bonus gets counted twice. This module is the deliberate version of that join, for an
operator who has the database and has confirmed both identities:

* ``audit`` lists the chat accounts of a messenger (Telegram and MAX alike), their
  history, their money, and the addresses they could belong to;
* ``plan_merge`` says exactly what would move and refuses on anything it cannot do
  without losing or duplicating something;
* ``apply_merge`` moves the chat's alias, its conversations and messages into the target,
  neutralises the source's identity, disables the source and revokes its keys.

Money never moves. The allowance already granted to the source stays on the disabled
account, and the target's balance is not touched, so no free grant is added twice. Nothing
is deleted: the source row remains for the operator's ledger, reachable by nobody.

The live database is not opened here by itself; the CLI takes a backup first, prints the
plan, and only applies it when the operator names both accounts and says yes.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .db import Database
from .service import CHANNEL_SURFACES, chat_identity


class MergeError(RuntimeError):
    """The plan cannot be applied without losing or duplicating something."""


@dataclass
class MergePlan:
    """Everything that would move, worked out before a single row is written."""

    target: dict[str, Any]
    source: dict[str, Any]
    surface: str
    chat_id: str
    id_hash: str
    identity_hashes: tuple[str, ...]
    alias: dict[str, Any] | None
    target_main: str | None
    source_main: str | None
    conversations: list[dict[str, Any]] = field(default_factory=list)
    messages: int = 0
    keys: int = 0
    warnings: list[str] = field(default_factory=list)

    def describe(self) -> str:
        target, source = self.target, self.source
        lines = [
            f"target {str(target['id'])[:8]} {target['channel']} {target['hint']}"
            f" (created {time.strftime('%Y-%m-%d', time.localtime(int(target['created_at'])))})",
            f"source {str(source['id'])[:8]} {source['channel']} {source['hint']}"
            f" (created {time.strftime('%Y-%m-%d', time.localtime(int(source['created_at'])))})",
            f"chat   {self.surface}:{self.chat_id}",
            f"alias  {'moved' if self.alias is not None else 'made'} -> target",
            f"chats  {len(self.conversations)} conversation(s), {self.messages} message(s)",
            f"main   source {self.source_main or '-'} -> target {self.target_main or '(new)'}",
            f"keys   {self.keys} revoked on the source",
            "money  untouched: the source keeps its grant and balance, disabled",
        ]
        lines.extend(f"warn   {line}" for line in self.warnings)
        return "\n".join(lines)


def _row(connection: sqlite3.Connection, sql: str, args: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    found = connection.execute(sql, args).fetchone()
    return dict(found) if found is not None else None


def _account(connection: sqlite3.Connection, account_id: str) -> dict[str, Any] | None:
    return _row(connection, "SELECT * FROM accounts WHERE id=?", (account_id,))


def _alias(connection: sqlite3.Connection, hashes: tuple[str, ...]) -> dict[str, Any] | None:
    for value in hashes:
        found = _row(connection, "SELECT * FROM account_aliases WHERE id_hash=?", (value,))
        if found is not None:
            return found
    return None


def _chat_hashes(surface: str, chat_id: str, hmac_key: bytes) -> tuple[str, ...]:
    """The canonical hash of the chat, then the pre-0.30 Telegram-space one for MAX."""
    primary = chat_identity(surface, chat_id).hash(hmac_key)
    if surface == "telegram":
        return (primary,)
    legacy = chat_identity("telegram", chat_id).hash(hmac_key)
    return (primary,) if legacy == primary else (primary, legacy)


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise MergeError(message)


def plan_merge(
    db: Database,
    *,
    target_id: str,
    source_id: str,
    surface: str,
    chat_id: str,
    hmac_key: bytes,
) -> MergePlan:
    """Work out the merge, refusing anything ambiguous.

    The target is the account the person keeps (signed in with an address, with devices).
    The source is the account the chat made: it must have no address of its own, and it
    must be the one that owns this chat identity.
    """
    surface = (surface or "").strip().lower()
    _check(surface in CHANNEL_SURFACES, "the messenger is telegram or max")
    _check(bool(chat_id.strip()), "a chat id is required")
    _check(target_id != source_id, "target and source are the same account")
    hashes = _chat_hashes(surface, str(chat_id).strip(), hmac_key)
    with db.tx() as connection:
        target = _account(connection, target_id)
        source = _account(connection, source_id)
        _check(target is not None, f"no target account {target_id}")
        _check(source is not None, f"no source account {source_id}")
        assert target is not None and source is not None
        _check(not target["disabled"], "the target account is disabled")
        _check(not source["disabled"], "the source account is already disabled")
        linked = connection.execute(
            "SELECT COUNT(*) FROM account_aliases WHERE account_id=? AND channel IN ('email','phone')",
            (source_id,),
        ).fetchone()[0]
        _check(
            str(source["channel"]) in CHANNEL_SURFACES and not int(linked) and not str(source["password_hash"] or ""),
            "the source has its own sign-in (an address, a phone or a password), so it is not just the chat's account",
        )
        alias = _alias(connection, hashes)
        if alias is not None:
            _check(
                str(alias["account_id"]) == source_id,
                f"the chat already belongs to another account {str(alias['account_id'])[:8]}",
            )
        else:
            _check(
                str(source["id_hash"]) in hashes,
                "the source account does not own this chat identity",
            )
        target_conversations = {
            str(row["cid"]): dict(row)
            for row in connection.execute(
                "SELECT * FROM sync_conversations WHERE account_id=? AND deleted=0",
                (target_id,),
            )
        }
        target_main = next(
            (str(row["cid"]) for row in target_conversations.values() if row["kind"] == "main"),
            None,
        )
        conversations = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM sync_conversations WHERE account_id=? Order BY created_at",
                (source_id,),
            )
        ]
        source_main = next((str(row["cid"]) for row in conversations if row["kind"] == "main"), None)
        warnings: list[str] = []
        if target_main and source_main:
            warnings.append("both sides have a main chat: the source's messages join the target's main")
        for conversation in conversations:
            if conversation["kind"] == "main" and target_main:
                continue
            _check(
                str(conversation["cid"]) not in target_conversations,
                f"conversation id {str(conversation['cid'])[:8]} exists on both accounts",
            )
        messages = int(connection.execute("SELECT COUNT(*) FROM sync_messages WHERE account_id=?", (source_id,)).fetchone()[0])
        keys = int(connection.execute("SELECT COUNT(*) FROM api_keys WHERE account_id=?", (source_id,)).fetchone()[0])
        source_balance = int(source["grant_uy"]) + int(source["paid_balance_uy"])
        if source_balance:
            warnings.append(
                f"the source keeps {source_balance} µ¥ (grant {source['grant_uy']}, paid {source['paid_balance_uy']}); it is not moved"
            )
        return MergePlan(
            target=target,
            source=source,
            surface=surface,
            chat_id=str(chat_id).strip(),
            id_hash=alias["id_hash"] if alias is not None else hashes[0],
            identity_hashes=hashes,
            alias=alias,
            target_main=target_main,
            source_main=source_main,
            conversations=conversations,
            messages=messages,
            keys=keys,
            warnings=warnings,
        )


def backup_database(path: str, destination: str | Path) -> Path:
    """A consistent copy of the database, WAL and all, next to the file or where asked."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(path)
    try:
        target = sqlite3.connect(str(destination))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    try:
        destination.chmod(0o600)
    except OSError:  # pragma: no cover - windows
        pass
    return destination


def apply_merge(db: Database, plan: MergePlan) -> dict[str, Any]:
    """Move the identity and the history, then disable the source. One transaction.

    A crash rolls everything back; the backup taken by the caller is the second line of
    defence. The source's keys are revoked, which also drops its browser sessions and the
    chat keys it remembered, so nothing keeps spending the disabled account.
    """
    target_id = str(plan.target["id"])
    source_id = str(plan.source["id"])
    stamp = int(time.time())
    merged_hash = "merged:" + uuid.uuid4().hex
    moved_conversations = 0
    moved_messages = 0
    with db.tx() as connection:
        # these reference the rows that are about to change key; they go first
        for table in ("sync_leases", "sync_deliveries"):
            connection.execute(f"DELETE FROM {table} WHERE account_id=?", (source_id,))
        # the chat's identity: the row that makes the bot find the target from now on
        if plan.alias is None:
            connection.execute(
                "INSERT INTO account_aliases(id_hash,account_id,channel,hint,created_at,surface,identifier) VALUES(?,?,?,?,?,?,?)",
                (
                    plan.id_hash,
                    target_id,
                    plan.surface,
                    chat_identity(plan.surface, plan.chat_id).hint,
                    stamp,
                    plan.surface,
                    "",
                ),
            )
        else:
            connection.execute(
                "UPDATE account_aliases SET account_id=?, channel=?, surface=? WHERE id_hash=?",
                (target_id, plan.surface, plan.surface, plan.alias["id_hash"]),
            )
        # the old account must not keep answering for the chat by its own hash
        if str(plan.source["id_hash"]) in plan.identity_hashes:
            connection.execute(
                "UPDATE accounts SET id_hash=? WHERE id=? AND id_hash=?",
                (merged_hash, source_id, str(plan.source["id_hash"])),
            )
        # the account's change counter: moved messages land above everything the target has
        cursor = connection.execute("SELECT seq FROM sync_cursors WHERE account_id=?", (target_id,)).fetchone()
        lowest = connection.execute("SELECT MAX(seq) FROM sync_messages WHERE account_id=?", (target_id,)).fetchone()[0]
        seq = max(int(cursor[0]) if cursor is not None else 0, int(lowest or 0))
        target_main = plan.target_main
        for conversation in plan.conversations:
            cid = str(conversation["cid"])
            destination = target_main if conversation["kind"] == "main" and target_main else cid
            rows = connection.execute(
                "SELECT * FROM sync_messages WHERE account_id=? AND cid=? ORDER BY seq, created_at",
                (source_id, cid),
            ).fetchall()
            for message in rows:
                mid = str(message["mid"])
                exists = connection.execute("SELECT 1 FROM sync_messages WHERE account_id=? AND mid=?", (target_id, mid)).fetchone()
                if exists is not None:
                    continue  # the echo of a message both sides already have
                seq += 1
                connection.execute(
                    "UPDATE sync_messages SET account_id=?, cid=?, seq=? WHERE account_id=? AND mid=?",
                    (target_id, destination, seq, source_id, mid),
                )
                moved_messages += 1
            if conversation["kind"] == "main" and target_main:
                # its messages now live in the target's main; the empty source row goes
                connection.execute(
                    "DELETE FROM sync_conversations WHERE account_id=? AND cid=?",
                    (source_id, cid),
                )
            else:
                connection.execute(
                    "UPDATE sync_conversations SET account_id=? WHERE account_id=? AND cid=?",
                    (target_id, source_id, cid),
                )
                moved_conversations += 1
                if conversation["kind"] == "main":
                    target_main = cid
        # the source's own cursor goes with the account's active state
        connection.execute("DELETE FROM sync_cursors WHERE account_id=?", (source_id,))
        if cursor is None:
            connection.execute("INSERT INTO sync_cursors(account_id,seq) VALUES(?,?)", (target_id, seq))
        else:
            connection.execute("UPDATE sync_cursors SET seq=? WHERE account_id=?", (seq, target_id))
        # the disabled account spends nothing: its keys go, with their sessions
        connection.execute("DELETE FROM api_keys WHERE account_id=?", (source_id,))
        connection.execute("UPDATE accounts SET disabled=1 WHERE id=?", (source_id,))
        detail = f"source {source_id[:8]} {plan.surface}:{plan.chat_id}"
        connection.execute(
            "INSERT INTO events(account_id, ts, kind, detail) VALUES (?,?,?,?)",
            (target_id, stamp, "account.merged", detail),
        )
        connection.execute(
            "INSERT INTO events(account_id, ts, kind, detail) VALUES (?,?,?,?)",
            (source_id, stamp, "account.merged", f"into {target_id[:8]} {plan.surface}:{plan.chat_id}"),
        )
    return {
        "target": target_id,
        "source": source_id,
        "conversations": moved_conversations,
        "messages": moved_messages,
        "keys": plan.keys,
    }


def audit(db: Database, *, surface: str | None = None) -> list[dict[str, Any]]:
    """Chat accounts, their history and money, for a person who wants to see the split."""
    surfaces = (surface,) if surface else CHANNEL_SURFACES
    marks = ", ".join("?" for _ in surfaces)
    with db.tx() as connection:
        rows = connection.execute(
            f"SELECT * FROM accounts WHERE channel IN ({marks}) ORDER BY created_at",
            surfaces,
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            account_id = str(row["id"])
            conversations = connection.execute(
                "SELECT COUNT(*) FROM sync_conversations WHERE account_id=? AND deleted=0",
                (account_id,),
            ).fetchone()[0]
            messages = connection.execute("SELECT COUNT(*) FROM sync_messages WHERE account_id=?", (account_id,)).fetchone()[0]
            aliases = connection.execute("SELECT surface,identifier FROM account_aliases WHERE account_id=?", (account_id,)).fetchall()
            out.append(
                {
                    "id": account_id,
                    "channel": str(row["channel"]),
                    "hint": str(row["hint"]),
                    "created_at": int(row["created_at"]),
                    "disabled": bool(row["disabled"]),
                    "grant_uy": int(row["grant_uy"]),
                    "paid_balance_uy": int(row["paid_balance_uy"]),
                    "conversations": int(conversations),
                    "messages": int(messages),
                    "aliases": [f"{a['surface']}{a['identifier']}" for a in aliases],
                }
            )
        return out
