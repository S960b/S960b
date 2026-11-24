#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33d-shadow-roundtrip
[[ "$(id -un)" == kali && "$HOME" == /home/kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE33D_STOP_WRONG_CONTEXT >&2; exit 2; }
sha256sum -c SHA256SUMS >/dev/null
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python "$ROOT/shadow33d.py" --verify-only
