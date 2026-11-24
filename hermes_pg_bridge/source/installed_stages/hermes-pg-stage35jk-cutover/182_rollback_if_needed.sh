#!/usr/bin/env bash
# Run ONLY on owner's express instruction or when a remote-plugin check fails.
set -Eeuo pipefail
set +x
umask 077
[[ $(id -un) == kali && $HOME == /home/kali ]] || exit 2
cd /home/kali/hermes-pg-stage35jk-cutover
exec /home/kali/bridge-pg-v2/.venv/bin/python -B stage35jk.py rollback
