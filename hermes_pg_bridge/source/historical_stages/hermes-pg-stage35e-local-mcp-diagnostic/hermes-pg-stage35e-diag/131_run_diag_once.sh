#!/usr/bin/env bash
# One separate diagnostic 35E attempt. NEVER retries 35D's 121.
set -Eeuo pipefail
set +x
umask 077
[[ $(id -un) == kali && "$HOME" == /home/kali ]] || { echo STAGE35E_STOP_NOT_KALI; exit 2; }
cd /home/kali/hermes-pg-stage35e-diag
sha256sum -c SHA256SUMS >/dev/null
rc=0
PYTHONDONTWRITEBYTECODE=1 /home/kali/bridge-pg-v2/.venv/bin/python stage35e.py once || rc=$?
# Always print only allowlisted sanitised ASGI events, including on failure.
PYTHONDONTWRITEBYTECODE=1 /home/kali/bridge-pg-v2/.venv/bin/python log_audit35e.py
exit "$rc"
