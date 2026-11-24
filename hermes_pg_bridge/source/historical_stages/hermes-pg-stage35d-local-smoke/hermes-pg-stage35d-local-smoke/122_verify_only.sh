#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
cd /home/kali/hermes-pg-stage35d-local-smoke
[[ "$(id -un)" == kali ]] || exit 2
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python stage35d.py verify-live
echo STAGE35D_VERIFY_ONLY_COMPLETE_18773_UNCHANGED
