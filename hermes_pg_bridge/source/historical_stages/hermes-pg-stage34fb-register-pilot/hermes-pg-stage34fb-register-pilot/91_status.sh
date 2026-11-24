#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34fb-register-pilot
cd "$ROOT"
PY=/home/kali/bridge-pg-v2/.venv/bin/python
env -u APPDIR -u APPIMAGE "$PY" handoff34fb.py status
systemctl --user show hermes-pg-stage34fb-handoff.service -p ActiveState -p SubState -p Result --no-pager || true
