#!/usr/bin/env bash
# Execute on Kali. The only persistent changes are 2 NOLOGIN DB roles and 9 wrappers.
set -Eeuo pipefail
set +x
umask 077

fail(){ printf 'STAGE33_STOP_%s\n' "$1" >&2; exit 2; }
[[ "$(id -un)" == 'kali' && "$HOME" == '/home/kali' ]] || fail WRONG_USER
ROOT="$(cd -- "$(dirname -- "$0")" && pwd -P)"
[[ -s "$ROOT/sql/004_shadow_roles.sql" && -s "$ROOT/sql/005_verify_shadow.sql" ]] || fail PACKAGE_MISSING
[[ -n "${PG_V2_SSH_TARGET:-}" ]] || fail PRIVATE_SSH_TARGET_MISSING

# Verify the server is reachable without echoing root host details.
ssh -o StrictHostKeyChecking=yes -o BatchMode=yes -o ConnectTimeout=8 -- "$PG_V2_SSH_TARGET" \
  'test "$(id -u)" -eq 0 && test "$(runuser -u postgres -- psql -X -A -t -d bridge -c "SELECT current_database()")" = bridge' \
  || fail REMOTE_DATABASE_PREFLIGHT
printf '%s\n' STAGE33_REMOTE_DB_PREFLIGHT_OK

# One-shot, deliberate non-idempotent DDL; psql exits on the first SQL error.
# All SQL actions are inside a single BEGIN/COMMIT, and include catalog-level checks.
ssh -o StrictHostKeyChecking=yes -o BatchMode=yes -o ConnectTimeout=8 -- "$PG_V2_SSH_TARGET" \
  'runuser -u postgres -- psql -X -q -v ON_ERROR_STOP=1 -d bridge' \
  < "$ROOT/sql/004_shadow_roles.sql" || fail SHADOW_DDL_APPLY_FAILED_STOP_NO_RETRY
printf '%s\n' STAGE33_SHADOW_SQL_APPLIED

# Read-only verification (the only returned information is role ACLs and counts).
RAW="$(ssh -o StrictHostKeyChecking=yes -o BatchMode=yes -o ConnectTimeout=8 -- "$PG_V2_SSH_TARGET" \
   'runuser -u postgres -- psql -X -A -t -q -v ON_ERROR_STOP=1 -d bridge' \
   < "$ROOT/sql/005_verify_shadow.sql")" || fail SHADOW_VERIFY_SQL_FAILED
printf '%s\n' "$RAW" | python3 "$ROOT/verify33.py" || fail SHADOW_VERIFY_FAILED
printf '%s\n' STAGE33_SHADOW_ROLES_READY_NO_CLIENT_SWITCH
