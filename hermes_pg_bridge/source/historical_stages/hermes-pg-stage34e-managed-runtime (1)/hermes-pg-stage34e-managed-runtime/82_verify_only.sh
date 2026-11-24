#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34e-managed-runtime
PY=/home/kali/bridge-pg-v2/.venv/bin/python
NODE=/home/kali/.hermes/node/bin/node
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || exit 2
sha256sum -c SHA256SUMS >/dev/null
env -u APPDIR -u APPIMAGE "$PY" verify34e.py verify
"$NODE" --check "$ROOT/mt-pg-inbox-managed-stage34e-shadow.mjs"
printf '%s\n' STAGE34E_UNREGISTERED_CANDIDATE_VERIFIED_NO_LIVE_CHANGES
