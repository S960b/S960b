#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34c-managed-inbox
PY=/home/kali/bridge-pg-v2/.venv/bin/python
NODE=/home/kali/.hermes/node/bin/node
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE34C_STOP_CONTEXT >&2;exit 2; }
sha256sum -c SHA256SUMS >/dev/null
[[ -x "$NODE" ]] || { echo STAGE34C_STOP_NODE_MISSING >&2;exit 2; }
env -u APPDIR -u APPIMAGE "$PY" verify34c.py post_sql
env -u APPDIR -u APPIMAGE "$PY" render34c.py
"$NODE" --check "$ROOT/mt-pg-inbox-managed-stage34c.mjs"
echo STAGE34C_NODE_INERT_CANDIDATE_SYNTAX_OK
