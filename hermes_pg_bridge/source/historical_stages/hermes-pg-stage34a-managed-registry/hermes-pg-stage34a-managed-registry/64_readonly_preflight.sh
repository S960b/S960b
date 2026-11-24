#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34a-managed-registry
PY=/home/kali/bridge-pg-v2/.venv/bin/python
[[ "$(id -un)" == kali && "$HOME" == /home/kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE34A_STOP_INVALID_CONTEXT >&2;exit 2; }
[[ -x "$PY" ]] || { echo STAGE34A_STOP_ACTOR_RUNTIME_MISSING >&2;exit 2; }
sha256sum -c SHA256SUMS >/dev/null
for svc in bridge-pg-v2-ssh-shadow.service hermes-pg-v2-stage33fb-cg.service hermes-pg-tunnel-stage33fd.service bridge-pg-v2-taskboard.service; do
 systemctl --user is-active --quiet "$svc" || { echo STAGE34A_STOP_ACTIVE_SERVICE_MISSING >&2;exit 2; }
done
/usr/bin/pg_isready -h 127.0.0.1 -p 15433 -q || { echo STAGE34A_STOP_POSTGRES_TUNNEL_OFFLINE >&2;exit 2; }
env -u APPDIR -u APPIMAGE "$PY" "$ROOT/registry34a.py" preflight
printf '%s\n' STAGE34A_SERVICES_CANARY_AND_ROLES_PREFLIGHT_OK
