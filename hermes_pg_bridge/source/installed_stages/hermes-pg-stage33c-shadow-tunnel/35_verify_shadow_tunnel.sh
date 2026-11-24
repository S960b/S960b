#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
[[ "$(id -un)" == kali ]] || { echo STAGE33C_STOP_WRONG_USER >&2; exit 2; }
ROOT="$(cd "$(dirname "$0")" && pwd -P)"
[[ "$(systemctl --user is-active bridge-pg-v2-ssh-shadow.service)" == active ]] || { echo STAGE33C_STOP_SHADOW_UNIT_NOT_ACTIVE >&2; exit 2; }
[[ "$(systemctl --user is-enabled bridge-pg-v2-ssh-shadow.service)" == enabled ]] || { echo STAGE33C_STOP_SHADOW_UNIT_NOT_ENABLED >&2; exit 2; }
"/usr/bin/pg_isready" -h 127.0.0.1 -p 15433 -q || { echo STAGE33C_STOP_SHADOW_PORT_NOT_READY >&2; exit 2; }
cd "$ROOT"
sha256sum -c SHA256SUMS >/dev/null
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python "$ROOT/verify_shadow33c.py"
echo STAGE33C_READ_ONLY_SHADOW_FORWARD_VERIFIED
