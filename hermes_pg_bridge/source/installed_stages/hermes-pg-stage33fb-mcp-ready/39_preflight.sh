#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33fb-mcp-ready
PY=/home/kali/bridge-pg-v2/.venv/bin/python
[[ "$(id -un)" == kali && "$HOME" == /home/kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE33FB_STOP_INVALID_CONTEXT >&2; exit 2; }
[[ -x "$PY" ]] || { echo STAGE33FB_STOP_NO_PYTHON >&2; exit 2; }
[[ -f /home/kali/hermes-pg-stage33f-sdk-preflight/mt-pg-inbox-agent-stage33f.mjs ]] || { echo STAGE33FB_STOP_NO_33FA_CANDIDATE >&2;exit 2; }
[[ "$(systemctl --user is-active bridge-pg-v2-ssh-shadow.service)" == active ]] || { echo STAGE33FB_STOP_NO_SHADOW_SSH >&2; exit 2; }
env -u APPDIR -u APPIMAGE "$PY" inventory33fb.py
[[ ! -e opencode-original-snapshot.json && ! -e opencode-stage33f-draft.json ]] || { echo STAGE33FB_STOP_ALREADY_PREPARED >&2;exit 2; }
[[ ! -e "$HOME/.config/systemd/user/hermes-pg-v2-stage33fb-cg.service" ]] || { echo STAGE33FB_STOP_UNIT_EXISTS >&2;exit 2; }
! ss -lnt '( sport = :18773 )' | grep -q '127.0.0.1:18773' || { echo STAGE33FB_STOP_PORT_BUSY >&2;exit 2; }
echo STAGE33FB_PREFLIGHT_NO_CHANGES_OK
