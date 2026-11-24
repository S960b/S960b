#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
[[ "$(id -un)" == 'kali' ]] || { echo STAGE33_STOP_WRONG_USER >&2; exit 2; }
[[ -n "${PG_V2_SSH_TARGET:-}" ]] || { echo STAGE33_STOP_PRIVATE_SSH_TARGET_MISSING >&2; exit 2; }
ROOT="$(cd -- "$(dirname -- "$0")" && pwd -P)"
RAW="$(ssh -o StrictHostKeyChecking=yes -o BatchMode=yes -o ConnectTimeout=8 -- "$PG_V2_SSH_TARGET" \
   'runuser -u postgres -- psql -X -A -t -q -v ON_ERROR_STOP=1 -d bridge' \
   < "$ROOT/sql/005_verify_shadow.sql")" || { echo STAGE33_STOP_VERIFY_SQL >&2; exit 2; }
printf '%s\n' "$RAW" | python3 "$ROOT/verify33.py"
