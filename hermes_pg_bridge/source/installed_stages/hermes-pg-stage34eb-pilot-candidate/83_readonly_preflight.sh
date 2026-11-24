#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34eb-pilot-candidate
PY=/home/kali/bridge-pg-v2/.venv/bin/python
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE34EB_STOP_CONTEXT >&2; exit 2; }
sha256sum -c SHA256SUMS >/dev/null
for svc in bridge-pg-v2-ssh-shadow.service hermes-pg-tunnel-stage33fd.service bridge-pg-v2-taskboard.service; do
  systemctl --user is-active --quiet "$svc" || { echo STAGE34EB_STOP_REQUIRED_SERVICE >&2; exit 2; }
done
( cd /home/kali/hermes-pg-stage34e-managed-runtime &&
  env -u APPDIR -u APPIMAGE "$PY" verify34e.py verify )
env -u APPDIR -u APPIMAGE "$PY" pilot34eb.py preflight
echo STAGE34EB_STAGE34E_REAL_DB_AND_SEVEN_PLUGIN_PREFLIGHT_OK
echo STAGE34EB_READ_ONLY_PREPARE_READY
