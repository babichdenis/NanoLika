#!/usr/bin/env python3
"""Look at the relay's chat accounts, and (on purpose) join one to its person's account.

Run it inside the relay container, where the database path and the deployment's secrets
are already set:

    docker compose exec relay python scripts/merge_chat_account.py --audit
    docker compose exec relay python scripts/merge_chat_account.py --audit --surface max

    # a dry run: prints the plan and writes nothing
    docker compose exec relay python scripts/merge_chat_account.py \\
        --target <account-id> --source <account-id> --chat telegram:123456789

    # the real thing: a timestamped backup first, then one transaction
    docker compose exec relay python scripts/merge_chat_account.py \\
        --target <account-id> --source <account-id> --chat telegram:123456789 \\
        --apply --yes

`--target` is the account the person keeps (signed in with an address); `--source` is the
account the chat made. Both ids are shown by `--audit`. Before `--apply`, the person should
have signed in to both: the app (the target) and the chat (the source). The merge moves the
chat's identity and history, disables the source, and leaves its balance where it is.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# run from a checkout as well as from the installed package
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nanomuse_cloud.account_merge import (
    MergeError,
    apply_merge,
    audit,
    backup_database,
    plan_merge,
)
from nanomuse_cloud.config import Settings
from nanomuse_cloud.db import Database


def audit_text(rows: list[dict]) -> str:
    lines = ["id        channel   hint                 made        chats msgs  grant   paid  state"]
    for row in rows:
        made = time.strftime("%Y-%m-%d", time.localtime(row["created_at"]))
        lines.append(
            f"{row['id'][:8]}  {row['channel']:<8}  {row['hint']:<20} {made}  "
            f"{row['conversations']:>5} {row['messages']:>4}  {row['grant_uy']:>6}  "
            f"{row['paid_balance_uy']:>5}  {'disabled' if row['disabled'] else 'active'}"
        )
    if len(rows) <= 1:
        lines.append("")
        lines.append("one chat account, nothing to join")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit or merge a chat account into a person's account")
    parser.add_argument("--database", default="", help="the relay's database; CLOUD_DB by default")
    parser.add_argument("--audit", action="store_true", help="list the chat accounts and stop")
    parser.add_argument("--surface", choices=["telegram", "max"], help="only one messenger in the audit")
    parser.add_argument("--target", default="", help="the account that stays (the person's own)")
    parser.add_argument("--source", default="", help="the account the chat made")
    parser.add_argument("--chat", default="", help="the chat identity, e.g. telegram:123456789")
    parser.add_argument("--apply", action="store_true", help="write the plan (a backup is taken first)")
    parser.add_argument("--backup", default="", help="where the backup goes; beside the database by default")
    parser.add_argument("--yes", action="store_true", help="required with --apply")
    args = parser.parse_args(argv)

    settings = Settings()
    path = args.database or settings.database
    if not Path(path).is_file():
        print(f"no database at {path}; run this inside the relay container", file=sys.stderr)
        return 2
    database = Database(path)
    try:
        if args.audit or not (args.target and args.source and args.chat):
            rows = audit(database, surface=args.surface)
            print(audit_text(rows))
            if not (args.target and args.source and args.chat):
                print("\nadd --target, --source and --chat to see a merge plan")
            return 0
        surface, _, chat_id = args.chat.partition(":")
        plan = plan_merge(
            database,
            target_id=args.target,
            source_id=args.source,
            surface=surface,
            chat_id=chat_id,
            hmac_key=settings.hmac_key,
        )
        print(plan.describe())
        if not args.apply:
            print("\ndry run: nothing written; add --apply --yes to do it")
            return 0
        if not args.yes:
            print("\nrefusing to write without --yes", file=sys.stderr)
            return 2
        backup = Path(args.backup or f"{path}.backup-{time.strftime('%Y%m%d-%H%M%S')}")
        backup_database(path, backup)
        print(f"\nbackup {backup}")
        summary = apply_merge(database, plan)
        print("merged: {messages} message(s), {conversations} conversation(s), {keys} key(s) revoked; source is disabled".format(**summary))
        return 0
    except MergeError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    finally:
        database.close()


if __name__ == "__main__":
    raise SystemExit(main())
