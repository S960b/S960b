#!/usr/bin/env bash
# One-shot additive migration. On connection failure use VERIFY ONLY, never repeat.
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34b-managed-api
PY=/home/kali/bridge-pg-v2/.venv/bin/python
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE34B_STOP_CONTEXT >&2;exit 2; }
[[ -n "${PG_V2_SSH_TARGET:-}" ]] || { echo STAGE34B_STOP_PRIVATE_SSH_TARGET_MISSING >&2;exit 2; }
bash "$ROOT/67_readonly_preflight.sh"
ssh -o StrictHostKeyChecking=yes -o BatchMode=yes -o ConnectTimeout=8 -- "$PG_V2_SSH_TARGET" \
 'test "$(id -u)" -eq 0 && test "$(runuser -u postgres -- psql -X -A -t -d bridge -c "SELECT current_database()")" = bridge' \
 >/dev/null || { echo STAGE34B_STOP_OWNER_CONNECT_PREFLIGHT >&2;exit 2; }
echo STAGE34B_REMOTE_OWNER_PREFLIGHT_OK
ssh -o StrictHostKeyChecking=yes -o BatchMode=yes -o ConnectTimeout=8 -- "$PG_V2_SSH_TARGET" \
 'runuser -u postgres -- psql -X -q -v ON_ERROR_STOP=1 -d bridge' \
 < "$ROOT/sql/008_managed_api.sql" || { echo STAGE34B_STOP_DDL_OUTCOME_UNKNOWN_VERIFY_ONLY >&2;exit 2; }
echo STAGE34B_MANAGED_API_APPLIED
# All positive writes performed inside ONE transaction, then always ROLLBACK.
env -u APPDIR -u APPIMAGE "$PY" stage34b.py proof
echo STAGE34B_ROLLBACK_ONLY_PROOF_COMPLETE
