#!/usr/bin/env bash
# RECOVERY ONLY: if the SQL disable committed but the main script stopped.
# Does NOT retry cg_set_managed_dispatch. Not for routine execution.
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34g-quiesce-and-retire
cd "$ROOT"
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python stage34g.py revoke-after-confirmed-db
