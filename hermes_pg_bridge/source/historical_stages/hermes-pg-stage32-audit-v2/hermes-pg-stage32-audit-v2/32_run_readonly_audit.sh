#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077

[[ "$(id -un)" == kali && "$HOME" == /home/kali ]] || { echo 'STAGE32_STOP_WRONG_USER'; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd -P)"
BASE=/home/kali/bridge-pg-v2
PY="$BASE/.venv/bin/python"
[[ -x "$PY" && -f "$BASE/bridge_pg/store.py" ]] || { echo 'STAGE32_STOP_BRIDGE_NOT_INSTALLED'; exit 2; }
[[ "$(systemctl --user is-active hermes-pg-tunnel-one-shot.service)" == active ]] || { echo 'STAGE32_STOP_TUNNEL_INACTIVE'; exit 2; }
[[ "$(systemctl --user is-active hermes-pg-one-shot-canary.service)" == active ]] || { echo 'STAGE32_STOP_CANARY_INACTIVE'; exit 2; }
[[ "$(systemctl --user is-active bridge-pg-v2-taskboard.service)" == active ]] || { echo 'STAGE32_STOP_TASKBOARD_INACTIVE'; exit 2; }
# AppImage variables from interactive MultiTool sessions can poison the Python venv.
# Do not alter shell profile, systemd, venv, or any existing service.
/usr/bin/env -u APPDIR -u APPIMAGE PYTHONPATH="$BASE" "$PY" "$HERE/audit32.py"
echo 'STAGE32_AUDIT_ONLY_NO_SERVICE_OR_SCHEMA_CHANGES'
