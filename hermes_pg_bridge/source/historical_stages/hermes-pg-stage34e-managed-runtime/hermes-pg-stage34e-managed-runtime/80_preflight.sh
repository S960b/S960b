#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34e-managed-runtime
PY=/home/kali/bridge-pg-v2/.venv/bin/python
NODE=/home/kali/.hermes/node/bin/node
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE34E_STOP_CONTEXT >&2; exit 2; }
sha256sum -c SHA256SUMS >/dev/null
[[ -x "$PY" && -x "$NODE" ]] || { echo STAGE34E_STOP_RUNTIME_MISSING >&2; exit 2; }
for svc in bridge-pg-v2-ssh-shadow.service hermes-pg-tunnel-stage33fd.service bridge-pg-v2-taskboard.service; do
  systemctl --user is-active --quiet "$svc" || { echo STAGE34E_STOP_REQUIRED_SERVICE >&2; exit 2; }
done
[[ ! -e "$ROOT/mt-pg-inbox-managed-stage34e-shadow.mjs" ]] || { echo STAGE34E_STOP_ALREADY_BUILT >&2; exit 2; }
env -u APPDIR -u APPIMAGE "$PY" verify34e.py preflight
printf '%s\n' STAGE34E_PREFLIGHT_NO_DB_OR_RUNTIME_MUTATION
