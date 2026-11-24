#!/usr/bin/env bash
# Install a *temporary* one-shot write canary and switch one existing tunnel ID.
set -Eeuo pipefail
set +x
umask 077
[[ "$(id -un)" == kali && "$HOME" == /home/kali ]] || { echo STOP_wrong_user; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd -P)"
BASE=/home/kali/bridge-pg-v2
PY="$BASE/.venv/bin/python"
RUNROOT=/home/kali/hermes-pg-tunnel-one-shot-canary
UNITS=/home/kali/.config/systemd/user
SVC1=hermes-pg-one-shot-canary.service
SVC2=hermes-pg-tunnel-one-shot.service
PREVIOUS=hermes-pg-tunnel-probe.service
bash "$HERE/31_preflight.sh"

mkdir -m 0700 "$RUNROOT"
install -m 0600 "$HERE/canary_server.py" "$RUNROOT/canary_server.py"
install -m 0600 "$HERE/logic31.py" "$RUNROOT/logic31.py"
# Never copy the key, log it or hardcode it. Reuse the existing verified launcher
# and change only the local MCP/health ports. Same tunnel ID and secret path.
"$PY" - <<'PY'
from pathlib import Path
src=Path('/home/kali/hermes-pg-tunnel-readonly-probe/run_pg_tunnel.sh')
dst=Path('/home/kali/hermes-pg-tunnel-one-shot-canary/run_pg_tunnel.sh')
s=src.read_text()
old_a="export MCP_SERVER_URL='http://127.0.0.1:18767/mcp'"
old_b='exec "$BIN" run --health.listen-addr 127.0.0.1:18768'
assert s.count(old_a)==1 and s.count(old_b)==1, 'unexpected_prior_launcher_source'
assert "export MCP_OAUTH_DISCOVERY='disabled'" in s, 'auth_discovery_unexpected'
s=s.replace(old_a,"export MCP_SERVER_URL='http://127.0.0.1:18769/mcp'")
s=s.replace(old_b,'exec "$BIN" run --health.listen-addr 127.0.0.1:18770')
dst.write_text(s)
dst.chmod(0o700)
PY
bash -n "$RUNROOT/run_pg_tunnel.sh"
"$PY" -m py_compile "$RUNROOT/canary_server.py" "$RUNROOT/logic31.py"

cat > "$UNITS/$SVC1" <<'UNIT'
[Unit]
Description=Hermes PG fixed one-shot Secure MCP Tunnel write canary
After=network.target
[Service]
Type=simple
WorkingDirectory=/home/kali/hermes-pg-tunnel-one-shot-canary
Environment=PYTHONPATH=/home/kali/bridge-pg-v2:/home/kali/hermes-pg-tunnel-one-shot-canary
ExecStart=/home/kali/bridge-pg-v2/.venv/bin/python /home/kali/hermes-pg-tunnel-one-shot-canary/canary_server.py
Restart=on-failure
RestartSec=4
UMask=0077
NoNewPrivileges=yes
UNIT
cat > "$UNITS/$SVC2" <<'UNIT'
[Unit]
Description=OpenAI Secure MCP Tunnel to fixed one-shot PG canary
Wants=hermes-pg-one-shot-canary.service
After=hermes-pg-one-shot-canary.service network-online.target
[Service]
Type=simple
ExecStart=/home/kali/hermes-pg-tunnel-one-shot-canary/run_pg_tunnel.sh
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=yes
UNIT
chmod 0600 "$UNITS/$SVC1" "$UNITS/$SVC2"
systemctl --user daemon-reload
SUCCESS=0
on_exit() {
  if [[ "$SUCCESS" -ne 1 ]]; then
    echo STAGE31_SWITCH_FAILED_ROLLING_BACK >&2
    bash "$HERE/31_rollback.sh" || echo STAGE31_ROLLBACK_NEEDS_ATTENTION >&2
  fi
}
trap on_exit EXIT
systemctl --user start "$SVC1"
code=000
for _ in $(seq 1 45); do
  code="$(curl -sS --noproxy '*' --max-time 2 -o /dev/null -w '%{http_code}' http://127.0.0.1:18769/ping 2>/dev/null || true)"
  [[ "$code" == 200 ]] && break
  sleep 1
done
[[ "$code" == 200 ]] || { echo STOP_one_shot_server_not_ready; exit 1; }
echo STAGE31_FIXED_ONLY_SERVER_LOCAL_200
# Do not start two runtimes for same tunnel id.
systemctl --user stop "$PREVIOUS"
[[ "$(systemctl --user is-active "$PREVIOUS" || true)" != active ]] || { echo STOP_prior_tunnel_not_stopped; exit 1; }
systemctl --user start "$SVC2"
code=000
for _ in $(seq 1 60); do
  code="$(curl -sS --noproxy '*' --max-time 2 -o /dev/null -w '%{http_code}' http://127.0.0.1:18770/readyz 2>/dev/null || true)"
  [[ "$code" == 200 ]] && break
  sleep 2
done
[[ "$code" == 200 ]] || { echo STOP_one_shot_tunnel_not_ready; exit 1; }
[[ "$(systemctl --user is-active "$SVC2")" == active ]] || { echo STOP_tunnel_unit_not_active; exit 1; }
[[ "$(systemctl --user is-active bridge-pg-v2-mcp.service)" == active ]] || { echo STOP_original_pg_mcp_inactive; exit 1; }
[[ "$(systemctl --user is-active bridge-pg-v2-taskboard.service)" == active ]] || { echo STOP_original_taskboard_inactive; exit 1; }
SUCCESS=1
trap - EXIT
echo STAGE31_TUNNEL_WRITE_CANARY_READY_200
echo STAGE31_ONE_FIXED_MESSAGE_NO_ARBITRARY_WRITES
echo STAGE31_READY_FOR_CHATGPT_TEST
