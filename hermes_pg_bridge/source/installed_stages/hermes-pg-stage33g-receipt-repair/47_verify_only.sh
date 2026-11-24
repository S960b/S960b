#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
fail(){ echo "STAGE33G_STOP_$1" >&2;exit 2; }
[[ "$(id -un)" == kali && "$HOME" == /home/kali ]] || fail WRONG_USER
[[ -n "${PG_V2_SSH_TARGET:-}" ]] || fail PRIVATE_TARGET_MISSING
ROOT="$(cd -- "$(dirname -- "$0")" && pwd -P)"
RAW="$(ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=8 \
  -- "$PG_V2_SSH_TARGET" \
  'test "$(id -u)" -eq 0 && runuser -u postgres -- psql -X -q -A -t -v ON_ERROR_STOP=1 -d bridge' \
  < "$ROOT/sql/01_preflight_readonly.sql")" || fail POST_VERIFY_QUERY
printf '%s\n' "$RAW" | env -u APPDIR -u APPIMAGE python3 "$ROOT/verify33g.py" post || fail POST_VERIFY_MISMATCH
printf '%s\n' STAGE33G_VERIFICATION_COMPLETE
