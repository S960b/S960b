#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34g-quiesce-and-retire
cd "$ROOT"
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python stage34g.py status
systemctl --user show hermes-pg-stage34g-retire.service -p ActiveState -p SubState -p Result --no-pager || true
