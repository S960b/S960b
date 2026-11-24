#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34d-write-adapter
PY=/home/kali/bridge-pg-v2/.venv/bin/python
NODE=/home/kali/.hermes/node/bin/node
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE34D_STOP_CONTEXT >&2; exit 2; }
sha256sum -c SHA256SUMS >/dev/null
[[ -x "$PY" && -x "$NODE" ]] || { echo STAGE34D_STOP_RUNTIME_MISSING >&2; exit 2; }
[[ ! -e "$ROOT/mt-pg-inbox-managed-stage34d-shadow.mjs" ]] || { echo STAGE34D_STOP_ALREADY_PREPARED >&2; exit 2; }
for svc in bridge-pg-v2-ssh-shadow.service hermes-pg-tunnel-stage33fd.service bridge-pg-v2-taskboard.service; do
  systemctl --user is-active --quiet "$svc" || { echo STAGE34D_STOP_REQUIRED_SERVICE >&2; exit 2; }
done
env -u APPDIR -u APPIMAGE "$PY" probe34d.py preflight
"$NODE" --check /home/kali/hermes-pg-stage34c-managed-inbox-v2/mt-pg-inbox-managed-stage34c.mjs
printf '%s\n' STAGE34D_READ_ONLY_PREFLIGHT_OK
