#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33j-ack-recovery
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || exit 2
sha256sum -c SHA256SUMS >/dev/null
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python stage33j.py verify-only
echo STAGE33J_VERIFY_ONLY_DONE
