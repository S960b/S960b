#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34c-managed-inbox
PY=/home/kali/bridge-pg-v2/.venv/bin/python
[[ "$(id -un)" = kali && "$(pwd -P)" = "$ROOT" ]] || { echo STAGE34C_STOP_CONTEXT >&2; exit 2; }
sha256sum -c SHA256SUMS >/dev/null
[[ "$(systemctl --user is-active bridge-pg-v2-ssh-shadow.service)" == active ]] || { echo STAGE34C_STOP_SSH_FORWARD_OFFLINE >&2;exit 2; }
[[ "$(systemctl --user is-active hermes-pg-tunnel-stage33fd.service)" == active ]] || { echo STAGE34C_STOP_ACTIVE_TUNNEL_OFFLINE >&2;exit 2; }
[[ "$(systemctl --user is-active bridge-pg-v2-taskboard.service)" == active ]] || { echo STAGE34C_STOP_TASKBOARD_OFFLINE >&2;exit 2; }
env -u APPDIR -u APPIMAGE "$PY" verify34c.py preflight
printf '%s\n' STAGE34C_NO_LIVE_CLIENT_OR_DB_MUTATION_PREFLIGHT_OK
