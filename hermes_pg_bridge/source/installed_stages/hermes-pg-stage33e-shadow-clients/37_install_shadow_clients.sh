#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33e-shadow-clients
UNIT="$HOME/.config/systemd/user/hermes-pg-v2-shadow-cg.service"
[[ "$(id -un)" == kali && "$HOME" == /home/kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE33E_STOP_WRONG_CONTEXT >&2; exit 2; }
bash 37_preflight.sh
BEFORE_SHA="$(sha256sum "$HOME/.config/gigatool/opencode.json" | cut -d' ' -f1)"
created=0
cleanup() {
  rc=$?
  if (( rc != 0 && created == 1 )); then
    systemctl --user stop hermes-pg-v2-shadow-cg.service >/dev/null 2>&1 || true
    rm -f -- "$UNIT"
    systemctl --user daemon-reload >/dev/null 2>&1 || true
    rm -f -- "$ROOT/mt-pg-inbox-agent-shadow.mjs"
    echo STAGE33E_LOCAL_SHADOW_INSTALL_ROLLED_BACK >&2
  fi
}
trap cleanup EXIT

created=1
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python prepare_shadow_plugin.py
/home/kali/.hermes/node/bin/node --check "$ROOT/mt-pg-inbox-agent-shadow.mjs"
echo STAGE33E_NODE_PLUGIN_SYNTAX_OK
mkdir -p -m 0700 "$HOME/.config/systemd/user"
cat > "$UNIT" <<'UNITFILE'
[Unit]
Description=Hermes PG v2 actor-bound ChatGPT read-only shadow MCP (STAGE33E, no tunnel)
After=bridge-pg-v2-ssh-shadow.service
Wants=bridge-pg-v2-ssh-shadow.service

[Service]
Type=simple
WorkingDirectory=/home/kali/hermes-pg-stage33e-shadow-clients
ExecStart=/home/kali/bridge-pg-v2/.venv/bin/python /home/kali/hermes-pg-stage33e-shadow-clients/chatgpt_shadow_mcp.py
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
UMask=0077

[Install]
WantedBy=default.target
UNITFILE
chmod 0600 "$UNIT"
systemctl --user daemon-reload
systemctl --user start hermes-pg-v2-shadow-cg.service
ok=0
for n in $(seq 1 20); do
  if /usr/bin/curl --noproxy '*' --silent --fail --max-time 2 http://127.0.0.1:18772/ping > /dev/null; then ok=1;break;fi
  sleep 1
done
(( ok == 1 )) || { echo STAGE33E_STOP_SHADOW_HTTP_UNAVAILABLE >&2; exit 2; }
echo STAGE33E_LOOPBACK_SHADOW_MCP_PING_OK
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python verify33e.py
AFTER_SHA="$(sha256sum "$HOME/.config/gigatool/opencode.json" | cut -d' ' -f1)"
[[ "$BEFORE_SHA" == "$AFTER_SHA" ]] || { echo STAGE33E_STOP_PLUGIN_CONFIG_CHANGED >&2; exit 2; }
[[ "$(systemctl --user is-active hermes-pg-tunnel-one-shot.service)" == active ]] || { echo STAGE33E_STOP_TUNNEL_CHANGED >&2; exit 2; }
[[ "$(systemctl --user is-enabled hermes-pg-v2-shadow-cg.service 2>/dev/null || true)" != enabled ]] || { echo STAGE33E_STOP_SHADOW_UNIT_SHOULD_NOT_AUTOSTART >&2;exit 2; }
trap - EXIT
echo STAGE33E_SHADOW_CG_MCP_RUNNING_LOCAL_ONLY_18772
echo STAGE33E_EXISTING_SECURE_TUNNEL_AND_MT_CONFIG_UNCHANGED
echo STAGE33E_INSTALL_COMPLETE_NO_CLIENT_CUTOVER
