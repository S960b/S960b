#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33i-ackfix
test "$(id -un)" = kali
test "$PWD" = "$ROOT"
export PYTHONNOUSERSITE=1
PY=/home/kali/bridge-pg-v2/.venv/bin/python
test -x "$PY"
if ! command -v node >/dev/null; then echo STAGE33I_STOP_NODE_MISSING; exit 2; fi

echo '=== READ ONLY PREFLIGHT ==='
env -u APPDIR -u APPIMAGE "$PY" preflight33i.py

echo '=== CREATE UNREGISTERED CANDIDATES ==='
env -u APPDIR -u APPIMAGE "$PY" build33i.py

echo '=== SYNTAX AND FUNCTIONAL RECONCILIATION TESTS ==='
env -u APPDIR -u APPIMAGE "$PY" -m py_compile stage33i_agent_cli.py
node --check mt-pg-inbox-agent-stage33i.mjs
node test_reconcile33i.cjs "$ROOT/mt-pg-inbox-agent-stage33i.mjs"

echo '=== INDEPENDENT VERIFY ==='
env -u APPDIR -u APPIMAGE "$PY" verify33i.py

echo STAGE33I_A_PREPARE_COMPLETE_NO_CUTOVER
