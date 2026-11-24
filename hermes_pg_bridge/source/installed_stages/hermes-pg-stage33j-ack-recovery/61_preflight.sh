#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33j-ack-recovery
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE33J_STOP_CONTEXT >&2; exit 2; }
sha256sum -c SHA256SUMS >/dev/null
for svc in bridge-pg-v2-ssh-shadow.service hermes-pg-v2-stage33fb-cg.service hermes-pg-tunnel-stage33fd.service; do
 systemctl --user is-active --quiet "$svc" || { echo STAGE33J_STOP_REQUIRED_SERVICE_OFFLINE >&2; exit 2; }
done
/usr/bin/pg_isready -h 127.0.0.1 -p 15433 -q || { echo STAGE33J_STOP_ACTOR_DB_TUNNEL_OFFLINE >&2; exit 2; }
PY=/home/kali/bridge-pg-v2/.venv/bin/python
NODE=/home/kali/.hermes/node/bin/node
[[ -x "$PY" && -x "$NODE" ]] || { echo STAGE33J_STOP_RUNTIME_MISSING >&2; exit 2; }
"$NODE" --check reconcile33j.cjs
"$NODE" /home/kali/hermes-pg-stage33i-ackfix/test_reconcile33i.cjs \
 /home/kali/hermes-pg-stage33i-ackfix/mt-pg-inbox-agent-stage33i.mjs
env -u APPDIR -u APPIMAGE "$PY" stage33j.py preflight
"$NODE" reconcile33j.cjs --preflight-only /home/kali/hermes-pg-stage33i-ackfix/mt-pg-inbox-agent-stage33i.mjs
echo STAGE33J_OFFLINE_FUNCTION_AND_DB_READONLY_PREFLIGHT_OK
