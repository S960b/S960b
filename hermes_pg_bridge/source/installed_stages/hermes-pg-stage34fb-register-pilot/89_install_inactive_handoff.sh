#!/usr/bin/env bash
# Prepare an INACTIVE one-shot config handoff; does not stop MultiTool or alter live config.
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34fb-register-pilot
PY=/home/kali/bridge-pg-v2/.venv/bin/python
NODE=/home/kali/.hermes/node/bin/node
UNIT="$HOME/.config/systemd/user/hermes-pg-stage34fb-handoff.service"
[[ "$(id -un)" == kali && "$HOME" == /home/kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE34FB_STOP_BAD_CONTEXT; exit 2; }
[[ -x "$PY" && -x "$NODE" ]] || { echo STAGE34FB_STOP_RUNTIME_MISSING; exit 2; }
[[ ! -e "$UNIT" && ! -L "$UNIT" ]] || { echo STAGE34FB_STOP_UNIT_ALREADY_EXISTS; exit 2; }
"$NODE" --check /home/kali/hermes-pg-stage34eb-pilot-candidate/mt-pg-inbox-managed-stage34eb-pilot-unregistered.mjs
echo STAGE34FB_NODE_PILOT_SYNTAX_OK
env -u APPDIR -u APPIMAGE "$PY" handoff34fb.py prepare
mkdir -p -m 700 "$HOME/.config/systemd/user"
cat > "$UNIT" <<'UNITFILE'
[Unit]
Description=Hermes PG Stage 34F-B add one disabled managed pilot after MultiTool exit
Wants=bridge-pg-v2-ssh-shadow.service
After=bridge-pg-v2-ssh-shadow.service

[Service]
Type=oneshot
WorkingDirectory=/home/kali/hermes-pg-stage34fb-register-pilot
ExecStart=/home/kali/bridge-pg-v2/.venv/bin/python /home/kali/hermes-pg-stage34fb-register-pilot/handoff34fb.py worker
TimeoutStartSec=1100
RemainAfterExit=yes
Restart=no
NoNewPrivileges=yes
Environment=PYTHONNOUSERSITE=1
UnsetEnvironment=APPDIR APPIMAGE
UMask=0077
UNITFILE
chmod 600 "$UNIT"
systemctl --user daemon-reload
[[ "$(systemctl --user is-enabled hermes-pg-stage34fb-handoff.service 2>/dev/null || true)" != enabled ]] || exit 2
if systemctl --user is-active --quiet hermes-pg-stage34fb-handoff.service; then exit 2; fi
echo STAGE34FB_HANDOFF_SERVICE_INSTALLED_INACTIVE
