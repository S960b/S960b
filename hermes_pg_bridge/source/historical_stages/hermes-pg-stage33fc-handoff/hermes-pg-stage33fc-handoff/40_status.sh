#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33fc-handoff
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || exit 2
echo '=== HANDOFF UNIT ==='
systemctl --user show hermes-pg-stage33fc-handoff.service -p LoadState -p ActiveState -p SubState -p Result --no-pager
echo '=== PRIVATE RESULT ==='
if [[ -f handoff-result.json ]]; then cat handoff-result.json; else echo STAGE33FC_RESULT_PENDING;fi
echo '=== SAFE PROCESS AND CONFIG INVENTORY ==='
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python cutover33fc.py inventory
