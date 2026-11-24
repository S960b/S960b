#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33h-cg-receipt
fail(){ echo "STAGE33H_STOP_$1" >&2;exit 2; }
[[ "$(id -un)" == kali && "$HOME" == /home/kali && "$(pwd -P)" == "$ROOT" ]] || fail WRONG_CONTEXT
[[ -n "${PG_V2_SSH_TARGET:-}" ]] || fail SSH_TARGET_NOT_SET
bash "$ROOT/50_preflight_readonly.sh"
echo STAGE33H_APPLY_EXACT_CHATGPT_REPORT_RECEIPT
ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=8 -- "$PG_V2_SSH_TARGET" \
 'test "$(id -u)" -eq 0 && runuser -u postgres -- psql -X -q -v ON_ERROR_STOP=1 -d bridge' \
 < "$ROOT/sql/02_accept_exact_seq6_once.sql" || fail RECEIPT_COMMIT_UNCERTAIN
printf '%s\n' STAGE33H_SEQ6_ONE_RECEIPT_TRANSACTION_COMMITTED
bash "$ROOT/52_verify_only.sh"
echo STAGE33H_NO_SCHEMA_OR_PLUGIN_CHANGES
