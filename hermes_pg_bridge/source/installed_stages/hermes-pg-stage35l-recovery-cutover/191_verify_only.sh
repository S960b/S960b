#!/usr/bin/env bash
# Repeatable read-only verification. No mutation.
set -Eeuo pipefail
set +x
umask 077
[[ $(id -un) == kali && $HOME == /home/kali && $EUID != 0 ]] || exit 2
cd /home/kali/hermes-pg-stage35l-recovery-cutover
exec /home/kali/bridge-pg-v2/.venv/bin/python -B stage35l.py verify
