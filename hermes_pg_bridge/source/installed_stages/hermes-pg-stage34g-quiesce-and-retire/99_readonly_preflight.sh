#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34g-quiesce-and-retire
[[ "$(id -un)" == kali && "$HOME" == /home/kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE34G_BAD_CONTEXT; exit 2; }
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python stage34g.py pre-disable
echo STAGE34G_READONLY_PREFLIGHT_COMPLETE
