#!/usr/bin/env bash
# Read-only even if the previous transaction/ACK commit result is unknown.
set -Eeuo pipefail
set +x
umask 077
cd /home/kali/hermes-pg-stage34fc-first-managed-roundtrip
PY=/home/kali/bridge-pg-v2/.venv/bin/python
env -u APPDIR -u APPIMAGE "$PY" stage34fc.py verify-only
echo STAGE34FC_READ_ONLY_VERIFY_COMPLETE
