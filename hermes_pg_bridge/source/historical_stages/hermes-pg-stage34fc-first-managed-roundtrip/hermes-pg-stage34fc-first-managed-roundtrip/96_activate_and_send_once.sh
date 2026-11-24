#!/usr/bin/env bash
# One-shot: journal, arm private managed CLI, enable dispatch and send exact task
# in ONE PostgreSQL transaction; NEVER blindly re-run on any failure.
set -Eeuo pipefail
set +x
umask 077
cd /home/kali/hermes-pg-stage34fc-first-managed-roundtrip
PY=/home/kali/bridge-pg-v2/.venv/bin/python
env -u APPDIR -u APPIMAGE "$PY" stage34fc.py send-once
echo STAGE34FC_ONE_SHOT_SUBMISSION_SCRIPT_COMPLETE
