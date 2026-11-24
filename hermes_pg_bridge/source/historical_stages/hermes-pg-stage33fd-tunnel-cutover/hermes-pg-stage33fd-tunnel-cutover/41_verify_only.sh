#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33fd-tunnel-cutover
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || exit 2
[[ -f switched.json ]] || exit 2
status="$(curl -sS --noproxy '*' --max-time 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:18774/readyz 2>/dev/null || true)"
[[ "$status" == 200 ]] || { echo STAGE33FD_STOP_HEALTH_DOWN >&2;exit 2; }
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python verify33fd.py switched
echo STAGE33FD_VERIFY_ONLY_NEW_TUNNEL_READY
