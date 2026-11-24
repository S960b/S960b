#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
[[ "$(id -un)" == 'kali' && "$HOME" == '/home/kali' ]] || { echo STAGE33B_STOP_WRONG_USER >&2; exit 2; }
[[ -n "${PG_V2_SSH_TARGET:-}" ]] || { echo STAGE33B_STOP_PRIVATE_SSH_TARGET_MISSING >&2; exit 2; }
ROOT="$(cd "$(dirname "$0")" && pwd -P)"
cd "$ROOT"
sha256sum -c SHA256SUMS >/dev/null
exec env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python "$ROOT/stage33b.py" verify
