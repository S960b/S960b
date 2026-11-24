#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
[[ "$(id -un)" == kali && "$HOME" == /home/kali ]] || exit 2
cd /home/kali/hermes-pg-stage35b-issuer-readiness
PY=/home/kali/bridge-pg-v2/.venv/bin/python
env -u APPDIR -u APPIMAGE "$PY" readiness35b.py preflight
echo STAGE35B_PREPARED_NO_ISSUER_NO_PUBLIC_SERVICE
