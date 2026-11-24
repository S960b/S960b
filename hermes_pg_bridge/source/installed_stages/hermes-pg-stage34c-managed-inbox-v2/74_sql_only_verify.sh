#!/usr/bin/env bash
# Safe to use after an uncertain migration. Does not depend on candidate.
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34c-managed-inbox-v2
PY=/home/kali/bridge-pg-v2/.venv/bin/python
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE34C_STOP_CONTEXT >&2;exit 2; }
sha256sum -c SHA256SUMS >/dev/null
env -u APPDIR -u APPIMAGE "$PY" verify34c.py post_sql
echo STAGE34C_SQL_VERIFY_ONLY_NO_WRITES
