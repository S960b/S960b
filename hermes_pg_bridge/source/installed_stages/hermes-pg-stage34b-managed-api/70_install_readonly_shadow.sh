#!/usr/bin/env bash
# Install a DIFFERENT local-only read-only MCP. Does not attach to Secure Tunnel.
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34b-managed-api
PY=/home/kali/bridge-pg-v2/.venv/bin/python
UNIT="$HOME/.config/systemd/user/hermes-pg-stage34b-shadow.service"
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE34B_STOP_CONTEXT >&2;exit 2; }
[[ ! -e "$UNIT" && ! -L "$UNIT" ]] || { echo STAGE34B_STOP_UNIT_EXISTS >&2;exit 2; }
bash "$ROOT/69_verify_only.sh" >/dev/null
[[ "$(curl --noproxy '*' -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:18775/ping || true)" == 000 ]] || { echo STAGE34B_STOP_PORT_ALREADY_USED >&2;exit 2; }
mkdir -p -m 0700 "$HOME/.config/systemd/user"
cat > "$UNIT" <<'UNITFILE'
[Unit]
Description=Hermes PG Stage34B managed-dialog read-only LOCAL shadow MCP
After=bridge-pg-v2-ssh-shadow.service
Wants=bridge-pg-v2-ssh-shadow.service

[Service]
Type=simple
WorkingDirectory=/home/kali/hermes-pg-stage34b-managed-api
ExecStart=/home/kali/bridge-pg-v2/.venv/bin/python /home/kali/hermes-pg-stage34b-managed-api/shadow_mcp34b.py
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=yes

UNITFILE
chmod 0600 "$UNIT"
trap 'rc=$?; if [ "$rc" -ne 0 ]; then systemctl --user stop hermes-pg-stage34b-shadow.service >/dev/null 2>&1 || true; rm -f -- "$UNIT"; systemctl --user daemon-reload >/dev/null 2>&1 || true; echo STAGE34B_SHADOW_ROLLED_BACK >&2; fi' EXIT
systemctl --user daemon-reload
systemctl --user start hermes-pg-stage34b-shadow.service
ready=0
for n in $(seq 1 25); do
 if curl -sS --noproxy '*' --fail --max-time 2 http://127.0.0.1:18775/ping >/dev/null 2>&1;then ready=1;break;fi
 sleep 1
done
[[ "$ready" == 1 ]] || { echo STAGE34B_STOP_LOCAL_SHADOW_NOT_READY >&2;exit 2; }
[[ "$(systemctl --user is-enabled hermes-pg-stage34b-shadow.service 2>/dev/null || true)" != enabled ]] || exit 2
printf '%s\n' STAGE34B_LOCAL_READONLY_MCP_18775_READY_NO_TUNNEL
trap - EXIT
