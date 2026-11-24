#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34d-write-adapter
PY=/home/kali/bridge-pg-v2/.venv/bin/python
NODE=/home/kali/.hermes/node/bin/node
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE34D_STOP_CONTEXT >&2; exit 2; }
sha256sum -c SHA256SUMS >/dev/null
env -u APPDIR -u APPIMAGE "$PY" probe34d.py verify
"$NODE" --check "$ROOT/mt-pg-inbox-managed-stage34d-shadow.mjs"
[[ "$(systemctl --user is-active hermes-pg-tunnel-stage33fd.service)" == active ]] || exit 2
printf '%s\n' STAGE34D_VERIFY_ONLY_NO_MCP_OR_SDK_SWITCH
