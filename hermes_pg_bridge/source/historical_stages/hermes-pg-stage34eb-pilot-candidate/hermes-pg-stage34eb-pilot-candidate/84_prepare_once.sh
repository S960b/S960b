#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34eb-pilot-candidate
PY=/home/kali/bridge-pg-v2/.venv/bin/python
NODE=/home/kali/.hermes/node/bin/node
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || exit 2
sha256sum -c SHA256SUMS >/dev/null
[[ -x "$PY" && -x "$NODE" ]] || { echo STAGE34EB_STOP_RUNTIME_MISSING >&2; exit 2; }
env -u APPDIR -u APPIMAGE "$PY" pilot34eb.py preflight
env -u APPDIR -u APPIMAGE "$PY" pilot34eb.py prepare
"$NODE" --check "$ROOT/mt-pg-inbox-managed-stage34eb-pilot-unregistered.mjs"
env -u APPDIR -u APPIMAGE "$PY" pilot34eb.py verify
echo STAGE34EB_ACTIVE_CAPABLE_SOURCE_SYNTAX_OK
echo STAGE34EB_PREPARE_COMPLETE_NO_REGISTRATION_OR_WRITE_ARM
