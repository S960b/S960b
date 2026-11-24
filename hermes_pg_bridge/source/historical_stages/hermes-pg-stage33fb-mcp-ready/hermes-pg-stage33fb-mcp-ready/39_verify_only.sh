#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33fb-mcp-ready
PY=/home/kali/bridge-pg-v2/.venv/bin/python
[[ "$(id -un)" == kali && "$HOME" == /home/kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE33FB_STOP_INVALID_CONTEXT >&2;exit 2; }
[[ "$(systemctl --user is-active hermes-pg-v2-stage33fb-cg.service)" == active ]] || { echo STAGE33FB_STOP_MCP_NOT_ACTIVE >&2;exit 2; }
[[ "$(systemctl --user is-active hermes-pg-tunnel-one-shot.service)" == active ]] || { echo STAGE33FB_STOP_LEGACY_TUNNEL_CHANGED >&2;exit 2; }
env -u APPDIR -u APPIMAGE "$PY" inventory33fb.py --installed
curl -sS --noproxy '*' --fail --max-time 2 http://127.0.0.1:18773/ping >/dev/null
echo STAGE33FB_VERIFY_ONLY_OK
