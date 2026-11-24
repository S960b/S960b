#!/usr/bin/env bash
# PREPARE ONLY: No changes to MT config, DB or Secure Tunnel.
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33fb-mcp-ready
UNIT="$HOME/.config/systemd/user/hermes-pg-v2-stage33fb-cg.service"
PY=/home/kali/bridge-pg-v2/.venv/bin/python
[[ "$(id -un)" == kali && "$HOME" == /home/kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE33FB_STOP_INVALID_CONTEXT >&2;exit 2; }
bash "$ROOT/39_preflight.sh" >/dev/null
env -u APPDIR -u APPIMAGE "$PY" prepare_cutover33fb.py
created=0
cleanup() {
  rc=$?
  if ((rc != 0 && created == 1)); then
    systemctl --user stop hermes-pg-v2-stage33fb-cg.service >/dev/null 2>&1 || true
    rm -f -- "$UNIT"
    systemctl --user daemon-reload >/dev/null 2>&1 || true
    echo STAGE33FB_NEW_UNIT_ROLLED_BACK_OTHER_SERVICES_NOT_TOUCHED >&2
  fi
}
trap cleanup EXIT
mkdir -p -m 0700 "$HOME/.config/systemd/user"
[[ ! -e "$UNIT" && ! -L "$UNIT" ]] || { echo STAGE33FB_STOP_UNIT_ALREADY_EXISTS >&2;exit 2; }
cat > "$UNIT" <<'UNITFILE'
[Unit]
Description=Hermes PG v2 Stage 33F-B fixed role-bound canary MCP (LOOPBACK ONLY, NOT TUNNELED)
Wants=bridge-pg-v2-ssh-shadow.service
After=bridge-pg-v2-ssh-shadow.service

[Service]
Type=simple
WorkingDirectory=/home/kali/hermes-pg-stage33fb-mcp-ready
ExecStart=/home/kali/bridge-pg-v2/.venv/bin/python /home/kali/hermes-pg-stage33fb-mcp-ready/cg_mcp33fb.py
Restart=on-failure
RestartSec=4
NoNewPrivileges=yes
UMask=0077

UNITFILE
chmod 600 "$UNIT"
created=1
systemctl --user daemon-reload
systemctl --user start hermes-pg-v2-stage33fb-cg.service
success=0
for _ in $(seq 1 25); do
    if curl -sS --noproxy '*' --fail --max-time 2 http://127.0.0.1:18773/ping >/dev/null 2>&1; then success=1;break;fi
    sleep 1
done
((success == 1)) || { echo STAGE33FB_STOP_MCP_NOT_READY >&2;exit 2; }
[[ "$(systemctl --user is-enabled hermes-pg-v2-stage33fb-cg.service 2>/dev/null || true)" != enabled ]] || { echo STAGE33FB_STOP_MCP_UNIT_ENABLED >&2;exit 2; }
env -u APPDIR -u APPIMAGE "$PY" inventory33fb.py --installed
echo STAGE33FB_NEW_MCP_18773_PING_OK
trap - EXIT
echo STAGE33FB_INSTALL_COMPLETE_NO_SDK_NO_TUNNEL_NO_CONFIG_SWITCH
