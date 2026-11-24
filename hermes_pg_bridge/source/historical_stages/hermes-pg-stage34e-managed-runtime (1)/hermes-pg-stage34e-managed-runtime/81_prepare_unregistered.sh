#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34e-managed-runtime
PY=/home/kali/bridge-pg-v2/.venv/bin/python
NODE=/home/kali/.hermes/node/bin/node
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || exit 2
sha256sum -c SHA256SUMS >/dev/null
env -u APPDIR -u APPIMAGE "$PY" verify34e.py preflight
env -u APPDIR -u APPIMAGE "$PY" render34e.py
"$NODE" --check "$ROOT/mt-pg-inbox-managed-stage34e-shadow.mjs"
"$NODE" --test "$ROOT/test_runtime34e.cjs"
env -u APPDIR -u APPIMAGE "$PY" verify34e.py verify
printf '%s\n' STAGE34E_PREPARE_COMPLETE_NO_CUTOVER_NO_SDK_NO_WRITES
