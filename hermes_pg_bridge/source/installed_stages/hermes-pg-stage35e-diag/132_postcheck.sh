#!/usr/bin/env bash
# Safe read-only status check. Does not reset failed systemd unit or reuse one-shot marker.
set -Eeuo pipefail
set +x
umask 077
[[ $(id -un) == kali && "$HOME" == /home/kali ]] || { echo STAGE35E_STOP_NOT_KALI; exit 2; }
cd /home/kali/hermes-pg-stage35e-diag
PYTHONDONTWRITEBYTECODE=1 /home/kali/bridge-pg-v2/.venv/bin/python stage35e.py postcheck
PYTHONDONTWRITEBYTECODE=1 /home/kali/bridge-pg-v2/.venv/bin/python log_audit35e.py
