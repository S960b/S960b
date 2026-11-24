#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34c-managed-inbox-v2
PY=/home/kali/bridge-pg-v2/.venv/bin/python
NODE=/home/kali/.hermes/node/bin/node
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE34C_STOP_CONTEXT >&2;exit 2; }
sha256sum -c SHA256SUMS >/dev/null
env -u APPDIR -u APPIMAGE "$PY" verify34c.py verify
"$NODE" --check "$ROOT/mt-pg-inbox-managed-stage34c.mjs"
[[ "$(systemctl --user is-active hermes-pg-tunnel-stage33fd.service)" == active ]] || exit 2
[[ "$(systemctl --user is-active bridge-pg-v2-ssh-shadow.service)" == active ]] || exit 2
[[ "$(systemctl --user is-active bridge-pg-v2-taskboard.service)" == active ]] || exit 2
printf '%s\n' STAGE34C_VERIFY_COMPLETE_NO_MT_OR_TUNNEL_SWITCH
