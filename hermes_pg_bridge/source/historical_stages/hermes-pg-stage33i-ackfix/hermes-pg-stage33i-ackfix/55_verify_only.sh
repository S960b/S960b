#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33i-ackfix
test "$(id -un)" = kali
test "$PWD" = "$ROOT"
node --check mt-pg-inbox-agent-stage33i.mjs
node test_reconcile33i.cjs "$ROOT/mt-pg-inbox-agent-stage33i.mjs"
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python verify33i.py
echo STAGE33I_A_VERIFY_ONLY_SUCCESS
