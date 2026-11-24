#!/usr/bin/env bash
# Add exactly 3 agent-only read functions, no dialogs or outbox writes.
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34c-managed-inbox-v2
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE34C_STOP_CONTEXT >&2;exit 2; }
[[ -n "${PG_V2_SSH_TARGET:-}" ]] || { echo STAGE34C_STOP_PRIVATE_SSH_TARGET_MISSING >&2;exit 2; }
bash 71_readonly_preflight.sh
ssh -o StrictHostKeyChecking=yes -o BatchMode=yes -o ConnectTimeout=8 -- "$PG_V2_SSH_TARGET" \
 'test "$(id -u)" -eq 0 && test "$(runuser -u postgres -- psql -X -A -t -d bridge -c "SELECT current_database()")" = bridge' \
 >/dev/null || { echo STAGE34C_STOP_REMOTE_OWNER_PREFLIGHT >&2;exit 2; }
print_marker=STAGE34C_REMOTE_OWNER_PREFLIGHT_OK
printf '%s\n' "$print_marker"
ssh -o StrictHostKeyChecking=yes -o BatchMode=yes -o ConnectTimeout=8 -- "$PG_V2_SSH_TARGET" \
 'runuser -u postgres -- psql -X -q -v ON_ERROR_STOP=1 -d bridge' \
 < "$ROOT/sql/009_managed_agent_read.sql" || { echo STAGE34C_STOP_SQL_OUTCOME_UNKNOWN_VERIFY_ONLY >&2;exit 2; }
echo STAGE34C_MANAGED_READ_API_APPLIED
