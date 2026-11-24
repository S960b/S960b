#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34b-managed-api
PY=/home/kali/bridge-pg-v2/.venv/bin/python
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE34B_STOP_CONTEXT >&2;exit 2; }
sha256sum -c SHA256SUMS >/dev/null
[[ "$(systemctl --user is-active bridge-pg-v2-ssh-shadow.service)" == active ]] || exit 2
[[ "$(systemctl --user is-active hermes-pg-tunnel-stage33fd.service)" == active ]] || exit 2
[[ "$(systemctl --user is-active bridge-pg-v2-taskboard.service)" == active ]] || exit 2
env -u APPDIR -u APPIMAGE "$PY" stage34b.py preflight
printf '%s\n' STAGE34B_WORKING_CLIENTS_PRESERVED_PREFLIGHT_OK
