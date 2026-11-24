#!/usr/bin/env bash
# Zero committed database writes; independently usable after uncertain remote SQL.
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34a-managed-registry
PY=/home/kali/bridge-pg-v2/.venv/bin/python
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE34A_STOP_INVALID_CONTEXT >&2;exit 2; }
sha256sum -c SHA256SUMS >/dev/null
env -u APPDIR -u APPIMAGE "$PY" "$ROOT/registry34a.py" verify
[[ "$(systemctl --user is-active hermes-pg-tunnel-stage33fd.service)" == active ]] || { echo STAGE34A_STOP_TUNNEL_CHANGED >&2;exit 2; }
[[ "$(systemctl --user is-active bridge-pg-v2-ssh-shadow.service)" == active ]] || { echo STAGE34A_STOP_SHADOW_CHANGED >&2;exit 2; }
printf '%s\n' STAGE34A_READ_ONLY_VERIFY_COMPLETE
printf '%s\n' STAGE34A_REGISTRY_READY_NO_DISPATCHER_OR_MCP_CUTOVER
