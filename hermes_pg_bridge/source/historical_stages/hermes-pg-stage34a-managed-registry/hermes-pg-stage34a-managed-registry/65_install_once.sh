#!/usr/bin/env bash
# One-shot, additive migration. Do NOT repeat if SSH/psql returns an ambiguous failure.
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34a-managed-registry
PY=/home/kali/bridge-pg-v2/.venv/bin/python
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE34A_STOP_INVALID_CONTEXT >&2;exit 2; }
[[ -n "${PG_V2_SSH_TARGET:-}" ]] || { echo STAGE34A_STOP_PRIVATE_SSH_TARGET_MISSING >&2;exit 2; }
[[ -r sql/006_managed_registry.sql ]] || { echo STAGE34A_STOP_SQL_UNAVAILABLE >&2;exit 2; }
# Re-run read-only preflight before first and only DDL attempt.
bash "$ROOT/64_readonly_preflight.sh"
ssh -o StrictHostKeyChecking=yes -o BatchMode=yes -o ConnectTimeout=8 -- "$PG_V2_SSH_TARGET" \
  'test "$(id -u)" -eq 0 && test "$(runuser -u postgres -- psql -X -A -t -d bridge -c "SELECT current_database()")" = bridge' \
  >/dev/null || { echo STAGE34A_STOP_REMOTE_DB_PREFLIGHT >&2;exit 2; }
printf '%s\n' STAGE34A_REMOTE_DB_OWNER_PREFLIGHT_OK
ssh -o StrictHostKeyChecking=yes -o BatchMode=yes -o ConnectTimeout=8 -- "$PG_V2_SSH_TARGET" \
  'runuser -u postgres -- psql -X -q -v ON_ERROR_STOP=1 -d bridge' \
  < "$ROOT/sql/006_managed_registry.sql" || { echo STAGE34A_STOP_DDL_RESULT_UNCONFIRMED_USE_VERIFY_ONLY >&2;exit 2; }
printf '%s\n' STAGE34A_MANAGED_REGISTRY_SQL_APPLIED
# This test creates a dialogue inside a transaction then ROLLS BACK. Nothing committed.
env -u APPDIR -u APPIMAGE "$PY" "$ROOT/registry34a.py" proof
printf '%s\n' STAGE34A_ROLLBACK_ONLY_ROLE_TEST_COMPLETE
