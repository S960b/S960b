#!/usr/bin/env bash
# Switch exactly one tunnel runtime. No DB writes, no SDK, no ARM file.
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33fd-tunnel-cutover
UNIT="$HOME/.config/systemd/user/hermes-pg-tunnel-stage33fd.service"
OLD=hermes-pg-tunnel-one-shot.service
NEW=hermes-pg-tunnel-stage33fd.service
[[ "$(id -un)" == kali && "$HOME" == /home/kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE33FD_STOP_BAD_CONTEXT;exit 2; }
[[ ! -e "$UNIT" && ! -L "$UNIT" ]] || { echo STAGE33FD_STOP_UNIT_EXISTS;exit 2; }
[[ ! -e switched.json && ! -L switched.json ]] || { echo STAGE33FD_STOP_ALREADY_SWITCHED;exit 2; }
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python verify33fd.py preflight
bash -n ./run_stage33fd_tunnel.sh
chmod 700 ./run_stage33fd_tunnel.sh
mkdir -p -m 700 "$HOME/.config/systemd/user"
cat > "$UNIT" <<'UNITFILE'
[Unit]
Description=OpenAI Secure MCP Tunnel to Stage 33F bridge_chatgpt bounded test MCP
Wants=hermes-pg-v2-stage33fb-cg.service
After=hermes-pg-v2-stage33fb-cg.service network-online.target

[Service]
Type=simple
ExecStart=/home/kali/hermes-pg-stage33fd-tunnel-cutover/run_stage33fd_tunnel.sh
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
UMask=0077
UNITFILE
chmod 600 "$UNIT"
systemctl --user daemon-reload
stopped_old=0
success=0
cleanup() {
  rc=$?
  if ((rc != 0 && success == 0)); then
    systemctl --user stop "$NEW" >/dev/null 2>&1 || true
    if ((stopped_old == 1)); then
      systemctl --user start "$OLD" >/dev/null 2>&1 || true
      echo STAGE33FD_ROLLBACK_OLD_TUNNEL_RESTART_REQUESTED >&2
    fi
    rm -f -- "$UNIT"
    systemctl --user daemon-reload >/dev/null 2>&1 || true
    echo STAGE33FD_NEW_TUNNEL_ROLLED_BACK_NO_DB_WRITE >&2
  fi
}
trap cleanup EXIT
systemctl --user stop "$OLD"
stopped_old=1
[[ "$(systemctl --user is-active "$OLD" || true)" != active ]] || { echo STAGE33FD_STOP_OLD_STILL_ACTIVE >&2;exit 2; }
# Allow control-plane old session to close before establishing its replacement.
sleep 2
systemctl --user start "$NEW"
ready=0
for _ in $(seq 1 45); do
  status="$(curl -sS --noproxy '*' --max-time 2 -o /dev/null -w '%{http_code}' http://127.0.0.1:18774/readyz 2>/dev/null || true)"
  if [[ "$status" == 200 ]]; then ready=1;break;fi
  sleep 2
done
((ready == 1)) || { echo STAGE33FD_STOP_TUNNEL_NOT_READY >&2;exit 2; }
[[ "$(systemctl --user is-active "$OLD" || true)" != active ]] || { echo STAGE33FD_STOP_DUPLICATE_RUNTIME >&2;exit 2; }
[[ "$(systemctl --user is-enabled "$NEW" 2>/dev/null || true)" != enabled ]] || { echo STAGE33FD_STOP_UNIT_UNEXPECTEDLY_ENABLED >&2;exit 2; }
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python verify33fd.py switched
printf '{"stage":"33F-D","tunnel_switched":true,"armed":false,"seq5_sent":false}\n' > switched.json
chmod 600 switched.json
success=1
trap - EXIT
echo STAGE33FD_ONE_TUNNEL_ONLY_ROLE_BOUND_18773_READY
 echo STAGE33FD_NOT_ARMED_NO_SEQ5_NO_SDK_EXECUTION
 echo STAGE33FD_TUNNEL_SWITCH_COMPLETE_PENDING_CHATGPT_PLUGIN
