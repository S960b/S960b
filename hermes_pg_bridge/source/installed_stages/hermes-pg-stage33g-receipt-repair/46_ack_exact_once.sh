#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
fail(){ echo "STAGE33G_STOP_$1" >&2;exit 2; }
[[ "$(id -un)" == kali && "$HOME" == /home/kali ]] || fail WRONG_USER
[[ -n "${PG_V2_SSH_TARGET:-}" ]] || fail PRIVATE_TARGET_MISSING
ROOT="$(cd -- "$(dirname -- "$0")" && pwd -P)"
[[ -s "$ROOT/sql/02_ack_verified_seq5_once.sql" ]] || fail MISSING_SQL
bash "$ROOT/45_readonly_preflight.sh"
echo STAGE33G_APPLY_GUARDED_ONE_DELIVERY_ACK
ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=8 \
 -- "$PG_V2_SSH_TARGET" \
 'test "$(id -u)" -eq 0 && runuser -u postgres -- psql -X -q -v ON_ERROR_STOP=1 -d bridge' \
 < "$ROOT/sql/02_ack_verified_seq5_once.sql" || fail GUARDED_ACK_ABORTED
printf '%s\n' STAGE33G_ONE_DELIVERY_TRANSACTION_COMMITTED
bash "$ROOT/47_verify_only.sh"
print_status='STAGE33G_NO_PLUGIN_OR_SERVICE_CHANGES'
printf '%s\n' "$print_status"
