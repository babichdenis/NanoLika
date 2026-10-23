#!/usr/bin/env bash
# NanoLika Cloud — move the relay's data from one server to another.
#
#   bash scripts/cloud-migrate.sh backup  root@old.example [--out FILE]
#   bash scripts/cloud-migrate.sh restore root@new.example FILE [--force]
#   bash scripts/cloud-migrate.sh verify  https://cloud.example.com
#
# `backup` reads the live relay through `sqlite3 .backup` (safe while it runs), streams one
# tarball with `data/`, the geo database, `.env` and `Caddyfile`, and leaves the relay
# untouched. `restore` unpacks it on the target, starts docker compose and waits for
# /healthz. `verify` checks a server that is already answering: health, public config, the
# console pages and the certificate's dates.
#
# What this does not do: change DNS, move the bot, or delete anything on the old server.
# The runbook is docs/cloud-migration-ru.md; it says when each step is safe.
#
# Needs: ssh to both hosts, curl, and sqlite3 or python3 on the source.
set -euo pipefail

REMOTE_DIR="${REMOTE_DIR:-/opt/nanomuse/cloud}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-90}"

say() { printf '%s\n' "$*"; }
die() { printf 'cloud-migrate: %s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

# --- backup ------------------------------------------------------------------------------
# The database is read with `.backup`, not copied: a plain copy of a SQLite file that is
# being written can arrive torn, and a torn account database is worse than none.
cmd_backup() {
  local host="${1:-}" out=""
  shift || usage 2
  while [ $# -gt 0 ]; do
    case "$1" in
      --out) [ $# -ge 2 ] || die "--out needs a file name"; out="$2"; shift ;;
      *) die "unknown option: $1" ;;
    esac
    shift
  done
  [ -n "$host" ] || usage 2
  [ -n "$out" ] || out="cloud-backup-$(date +%Y%m%d-%H%M%S).tar.gz"

  say "backup: $host:$REMOTE_DIR → $out"
  # One remote script: dump the database, then stream the tarball to stdout, so nothing
  # large is written to the source. The dump is removed on the way out whatever happens.
  # The tar runs from the parent of REMOTE_DIR, so the archive holds `cloud/...` and the
  # audit copies under `backups/`: those carry personal data and leave the old server too.
  ssh "$host" "REMOTE_DIR='$REMOTE_DIR' bash -s" >"$out" <<'REMOTE_TAR'
set -euo pipefail
PARENT="$(dirname "$REMOTE_DIR")"
BASE="$(basename "$REMOTE_DIR")"
cd "$PARENT"
[ -f "$BASE/data/cloud.db" ] || { echo "cloud-migrate: $REMOTE_DIR/data/cloud.db is missing" >&2; exit 1; }
trap 'rm -f "$BASE/data/cloud.db.copy"' EXIT
if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$BASE/data/cloud.db" ".backup '$BASE/data/cloud.db.copy'"
else
  BASE="$BASE" python3 -c "import os, sqlite3; b = os.environ['BASE']; s = sqlite3.connect(b + '/data/cloud.db'); d = sqlite3.connect(b + '/data/cloud.db.copy'); s.backup(d); d.close(); s.close()"
fi
echo "cloud-migrate: database dumped ($(du -h "$BASE/data/cloud.db.copy" | cut -f1))" >&2
# Everything the relay needs on the other side. The live database and its wal are left
# out on purpose: the dumped copy is the consistent one.
files="$BASE/.env $BASE/Caddyfile $BASE/data/cloud.db.copy"
for extra in "$BASE/data/ip2region_v4.xdb" "$BASE/backups" "backups"; do
  [ -e "$extra" ] && files="$files $extra"
done
tar czf - $files
REMOTE_TAR
  chmod 600 "$out"

  local size
  size="$(du -h "$out" | cut -f1)"
  [ "$size" != "0B" ] || { rm -f "$out"; die "backup is empty: is $host reachable and is the relay there?"; }
  say "backup: got $size in $out (mode 600: it carries the .env secrets)"
  say "backup: check what went in: tar tzf $out"
}

# --- restore -----------------------------------------------------------------------------
cmd_restore() {
  local host="${1:-}" file="${2:-}" force=0
  if [ $# -ge 2 ]; then shift 2; else usage 2; fi
  while [ $# -gt 0 ]; do
    case "$1" in
      --force) force=1 ;;
      *) die "unknown option: $1" ;;
    esac
    shift
  done
  [ -n "$host" ] && [ -n "$file" ] || usage 2
  [ -f "$file" ] || die "no such file: $file"
  tar tzf "$file" >/dev/null || die "$file is not a readable tarball"

  if [ "$force" -eq 0 ]; then
    local has_data
    has_data="$(ssh "$host" "test -f '$REMOTE_DIR/data/cloud.db' && echo yes || echo no")"
    [ "$has_data" = "no" ] || die "$host already has $REMOTE_DIR/data/cloud.db; pass --force to overwrite"
  fi

  say "restore: $file → $host:$REMOTE_DIR"
  ssh "$host" "mkdir -p '$(dirname "$REMOTE_DIR")'"
  ssh "$host" "tar xzf - -C '$(dirname "$REMOTE_DIR")'" <"$file"
  ssh "$host" "cd '$REMOTE_DIR' && mv -f data/cloud.db.copy data/cloud.db"

  say "restore: files are in place; starting the relay"
  ssh "$host" "cd '$REMOTE_DIR' && (docker compose up -d || docker-compose up -d)"

  say "restore: waiting for the relay on $host (up to ${HEALTH_TIMEOUT}s)"
  ssh "$host" "for i in \$(seq 1 $HEALTH_TIMEOUT); do
    if curl -fsS http://127.0.0.1:8787/healthz >/dev/null 2>&1; then echo 'restore: relay answers'; exit 0; fi
    sleep 1
  done; echo 'restore: relay did not answer in time; check docker compose logs relay' >&2; exit 1"

  say "restore: done. Next: point DNS at $host, then run verify."
}

# --- verify ------------------------------------------------------------------------------
# Read-only checks of a server that should already be serving. Every line either answers
# or does not; nothing here changes anything.
cmd_verify() {
  local base="${1:-}"
  [ -n "$base" ] || usage 2
  base="${base%/}"
  local failed=0

  check() { # check NAME PATH [EXPECTED_STATUS]
    local name="$1" path="$2" want="${3:-200}" code
    code="$(curl -s -o /dev/null -m 20 -w '%{http_code}' "$base$path" || echo 000)"
    if [ "$code" = "$want" ]; then
      printf 'ok    %-22s %s\n' "$name" "$code"
    else
      printf 'FAIL  %-22s %s (wanted %s)\n' "$name" "$code"
      failed=$((failed + 1))
    fi
  }

  say "verify: $base"
  check "health" "/healthz"
  check "public config" "/v1/config"
  check "console" "/app/"
  check "console admin" "/app/admin/"

  if command -v openssl >/dev/null 2>&1; then
    local host cert
    host="$(printf '%s' "$base" | sed -E 's#^https?://([^/:]+).*#\1#')"
    cert="$(echo | openssl s_client -servername "$host" -connect "$host:443" 2>/dev/null \
      | openssl x509 -noout -dates 2>/dev/null || true)"
    if [ -n "$cert" ]; then
      say "verify: certificate $host"
      printf '%s\n' "$cert" | sed 's/^/       /'
    else
      say "verify: could not read the certificate for $host"
    fi
  fi

  # The account side cannot be checked without a person: sign-in, codes and payment are
  # theirs to try. Saying so beats pretending the check passed.
  say "verify: sign-in, e-mail codes and payment need a person: sign in at $base/app/"
  [ "$failed" -eq 0 ] || die "verify: $failed check(s) failed"
  say "verify: all checks passed"
}

case "${1:-}" in
  backup) shift; cmd_backup "$@" ;;
  restore) shift; cmd_restore "$@" ;;
  verify) shift; cmd_verify "$@" ;;
  -h|--help|help) usage 0 ;;
  *) usage 2 ;;
esac
