#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34fb-register-pilot
cd "$ROOT"
PY=/home/kali/bridge-pg-v2/.venv/bin/python
env -u APPDIR -u APPIMAGE "$PY" handoff34fb.py verify
systemctl --user is-active --quiet hermes-pg-v2-stage33fb-cg.service
systemctl --user is-active --quiet hermes-pg-tunnel-stage33fd.service
echo STAGE34FB_POST_HANDOFF_VERIFIED
