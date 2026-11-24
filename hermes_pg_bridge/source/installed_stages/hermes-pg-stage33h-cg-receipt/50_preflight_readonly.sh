#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33h-cg-receipt
fail(){ echo "STAGE33H_STOP_$1" >&2;exit 2; }
[[ "$(id -un)" == kali && "$HOME" == /home/kali && "$(pwd -P)" == "$ROOT" ]] || fail WRONG_CONTEXT
[[ -n "${PG_V2_SSH_TARGET:-}" ]] || fail SSH_TARGET_NOT_SET
[[ "$(sha256sum /home/kali/.config/gigatool/opencode.json | cut -d' ' -f1)" == a8f11a25173b5e7f1290d97b4aedfaea24403256fa09c16144093e0f9d297cb3 ]] || fail CONFIG_CHANGED
systemctl --user is-active --quiet hermes-pg-tunnel-stage33fd.service || fail SECURE_TUNNEL_INACTIVE
systemctl --user is-active --quiet bridge-pg-v2-ssh-shadow.service || fail SHADOW_SSH_INACTIVE
RAW="$(ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=8 -- "$PG_V2_SSH_TARGET" 'test "$(id -u)" -eq 0 && runuser -u postgres -- psql -X -q -A -t -v ON_ERROR_STOP=1 -d bridge' < "$ROOT/sql/01_readonly_receipt.sql")" || fail PRECHECK_QUERY_FAILED
printf '%s\n' "$RAW" | env -u APPDIR -u APPIMAGE python3 "$ROOT/verify33h.py" pre || fail PRECHECK_MISMATCH
echo STAGE33H_READ_ONLY_PREFLIGHT_OK
