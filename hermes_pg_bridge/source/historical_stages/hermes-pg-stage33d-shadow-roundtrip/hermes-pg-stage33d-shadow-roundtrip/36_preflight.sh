#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33d-shadow-roundtrip
[[ "$(id -un)" == kali && "$HOME" == /home/kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE33D_STOP_WRONG_CONTEXT >&2; exit 2; }
[[ "$(systemctl --user is-active bridge-pg-v2-ssh-shadow.service)" == active ]] || { echo STAGE33D_STOP_SSH_SHADOW_NOT_ACTIVE >&2; exit 2; }
/usr/bin/pg_isready -h 127.0.0.1 -p 15433 -q || { echo STAGE33D_STOP_SSH_SHADOW_NOT_READY >&2; exit 2; }
[[ "$(systemctl --user is-active hermes-pg-tunnel-one-shot.service)" == active ]] || { echo STAGE33D_STOP_TUNNEL_CANARY_NOT_ACTIVE >&2; exit 2; }
[[ "$(systemctl --user is-active hermes-pg-one-shot-canary.service)" == active ]] || { echo STAGE33D_STOP_MCP_CANARY_NOT_ACTIVE >&2; exit 2; }
sha256sum -c SHA256SUMS >/dev/null
# Drop APPDIR/APPIMAGE because an interactive MultiTool AppImage can corrupt venv Python imports.
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python "$ROOT/shadow33d.py" --preflight
