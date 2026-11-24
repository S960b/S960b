#!/usr/bin/env bash
# No mutation, useful when disable COMMIT outcome is uncertain.
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34g-quiesce-and-retire
cd "$ROOT"
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python stage34g.py inspect-policy
