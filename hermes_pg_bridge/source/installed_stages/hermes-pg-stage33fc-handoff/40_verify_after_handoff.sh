#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33fc-handoff
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || exit 2
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python cutover33fc.py verify
echo STAGE33FC_VERIFY_COMPLETE_NO_SDK_OR_TUNNEL_SWITCH
