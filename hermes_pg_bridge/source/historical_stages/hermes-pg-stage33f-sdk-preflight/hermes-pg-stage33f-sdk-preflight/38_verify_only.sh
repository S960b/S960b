#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33f-sdk-preflight
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE33F_STOP_WRONG_CONTEXT >&2;exit 2; }
/home/kali/.hermes/node/bin/node --check mt-pg-inbox-agent-stage33f.mjs
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python inventory33f.py
echo STAGE33F_VERIFY_ONLY_COMPLETE
