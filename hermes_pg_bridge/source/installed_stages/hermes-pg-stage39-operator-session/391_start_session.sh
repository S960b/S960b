#!/usr/bin/env bash
# Operator-approved bounded session. Must not rearm on absent/invalid permits.
set -Eeuo pipefail
umask 077
ROOT=/home/kali/hermes-pg-stage39-operator-session
cd "$ROOT"
sha256sum -c SHA256SUMS >/dev/null
/usr/bin/python3 -I -B "$ROOT/stage39.py" start
