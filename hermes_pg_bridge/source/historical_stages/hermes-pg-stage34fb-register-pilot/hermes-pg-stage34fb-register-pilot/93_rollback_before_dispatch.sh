#!/usr/bin/env bash
# Use ONLY on separate owner instruction, with every MT window closed and pilot still disabled.
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34fb-register-pilot
cd "$ROOT"
PY=/home/kali/bridge-pg-v2/.venv/bin/python
env -u APPDIR -u APPIMAGE "$PY" handoff34fb.py rollback
