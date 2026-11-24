#!/usr/bin/env bash
# One irreversible *policy* attempt; a journal is written before SQL.
# NEVER repeat after error/unknown commit. Use verification/recovery only.
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34g-quiesce-and-retire
[[ "$(id -un)" == kali && "$HOME" == /home/kali && "$(pwd -P)" == "$ROOT" ]] || exit 2
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python stage34g.py disable-once
echo STAGE34G_PHASE_A_DISABLE_AND_REVOKE_SCRIPT_COMPLETE
