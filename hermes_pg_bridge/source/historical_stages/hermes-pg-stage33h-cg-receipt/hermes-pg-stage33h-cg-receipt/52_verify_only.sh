#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33h-cg-receipt
fail(){ echo "STAGE33H_STOP_$1" >&2;exit 2; }
[[ "$(id -un)" == kali && "$HOME" == /home/kali && "$(pwd -P)" == "$ROOT" ]] || fail WRONG_CONTEXT
[[ -n "${PG_V2_SSH_TARGET:-}" ]] || fail SSH_TARGET_NOT_SET
RAW="$(ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=8 -- "$PG_V2_SSH_TARGET" 'test "$(id -u)" -eq 0 && runuser -u postgres -- psql -X -q -A -t -v ON_ERROR_STOP=1 -d bridge' < "$ROOT/sql/01_readonly_receipt.sql")" || fail POSTCHECK_QUERY_FAILED
printf '%s\n' "$RAW" | env -u APPDIR -u APPIMAGE python3 "$ROOT/verify33h.py" post || fail POSTCHECK_MISMATCH
echo STAGE33H_READ_ONLY_POSTCHECK_COMPLETE
