#!/usr/bin/env bash
# Run only on owner's instruction if remote ChatGPT MCP check fails.
set -Eeuo pipefail
set +x
umask 077
[[ $(id -un) == kali && $HOME == /home/kali && $EUID != 0 ]] || exit 2
cd /home/kali/hermes-pg-stage35l-recovery-cutover
exec /home/kali/bridge-pg-v2/.venv/bin/python -B stage35l.py owner-rollback
