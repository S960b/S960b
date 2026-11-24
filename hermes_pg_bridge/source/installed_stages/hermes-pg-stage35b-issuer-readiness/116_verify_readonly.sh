#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
cd /home/kali/hermes-pg-stage35b-issuer-readiness
PY=/home/kali/bridge-pg-v2/.venv/bin/python
env -u APPDIR -u APPIMAGE "$PY" readiness35b.py verify
printf 'STAGE35B_FINAL_VERIFY_NO_SERVICE_OR_DATABASE_MUTATIONS\n'
