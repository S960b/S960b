#!/usr/bin/env bash
# Read-only. Doesn't create the attempt marker or start/stop any service.
set -Eeuo pipefail
set +x
umask 077
[[ $(id -un) == kali && "$HOME" == /home/kali ]] || { echo STAGE35E_STOP_NOT_KALI; exit 2; }
cd /home/kali/hermes-pg-stage35e-diag
sha256sum -c SHA256SUMS >/dev/null
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q test_stage35e
PYTHONDONTWRITEBYTECODE=1 /home/kali/bridge-pg-v2/.venv/bin/python stage35e.py preflight
printf '%s\n' STAGE35E_ARCHIVE_INTEGRITY_14_TESTS_AND_READONLY_PREFLIGHT_OK
