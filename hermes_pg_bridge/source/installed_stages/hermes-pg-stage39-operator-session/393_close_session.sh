#!/usr/bin/env bash
# Explicit operator stop is always permitted even after config drift.
set -Eeuo pipefail
umask 077
ROOT=/home/kali/hermes-pg-stage39-operator-session
/usr/bin/python3 -I -B "$ROOT/stage39.py" stop
