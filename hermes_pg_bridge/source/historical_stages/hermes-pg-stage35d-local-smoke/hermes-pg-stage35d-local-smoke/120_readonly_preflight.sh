#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
cd /home/kali/hermes-pg-stage35d-local-smoke
[[ "$(id -un)" == kali ]] || { echo STAGE35D_STOP_NOT_KALI;exit 2; }
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python stage35d.py preflight
echo STAGE35D_BINARY_CAPABILITY_PREFLIGHT_COMPLETE
