#!/usr/bin/env bash
set -Eeuo pipefail
set +x
cd /home/kali/hermes-pg-stage35b-issuer-readiness
PY=/home/kali/bridge-pg-v2/.venv/bin/python
env -u APPDIR -u APPIMAGE "$PY" readiness35b.py legacy
echo STAGE35B_LEGACY_STATIC_AUDIT_DONE
