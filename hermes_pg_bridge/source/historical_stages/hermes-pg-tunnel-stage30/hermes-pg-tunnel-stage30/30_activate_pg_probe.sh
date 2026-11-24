#!/usr/bin/env bash
# One-shot canary switch. DB/MCP/Taskboard/MT untouched; rollback on any failure.
set -Eeuo pipefail
set +x
umask 077
[[ "$(id -un)" == kali && "$HOME" == /home/kali ]] || { echo STOP_wrong_user; exit 2; }
HERE="$(cd -- "$(dirname -- "$0")" && pwd -P)"
PY=/home/kali/bridge-pg-v2/.venv/bin/python
BINDIR=/home/kali/.local/libexec/hermes-secure-tunnel-canary/v0.0.21
KEY=/home/kali/.config/hermes-secure-tunnel-canary/runtime.key
PROBE=/home/kali/hermes-pg-tunnel-readonly-probe
UNITS=/home/kali/.config/systemd/user
SVC1=hermes-pg-readonly-probe.service
SVC2=hermes-pg-tunnel-probe.service

[[ -x "$PY" && -x "$BINDIR/tunnel-client" && -f "$KEY" && ! -L "$KEY" ]] || { echo STOP_missing_prerequisites; exit 2; }
[[ "$(stat -c %a "$KEY")" == 600 ]] || { echo STOP_key_permissions; exit 2; }
[[ "$(systemctl --user is-active hermes-secure-tunnel-canary.service)" == active ]] || { echo STOP_old_demo_not_active; exit 2; }
for p in "$PROBE" "$UNITS/$SVC1" "$UNITS/$SVC2"; do
  [[ ! -e "$p" && ! -L "$p" ]] || { echo STOP_probe_already_exists; exit 2; }
done
if ss -ltn '( sport = :18767 or sport = :18768 )' | tail -n +2 | grep -q .; then
  echo STOP_probe_port_busy; exit 2
fi
PYTHONPATH=/home/kali/bridge-pg-v2 "$PY" - <<'PY'
from bridge_pg.store import Store
assert Store().health().get('ok') is True
from bridge_pg.compat import Catalog
v=Catalog().view('task_f2c64cfdf7894537b35a19a6def977c6',0,10,'newest')
assert v.get('total_messages') == 2
assert any(m.get('from')=='mt' and m.get('body')=='PGV2_CANARY_ACK_20261122' for m in v.get('history', []))
print('REAL_POSTGRESQL_AND_CANARY_VERIFIED')
PY

echo PRECHECKS_OK
mkdir -m 0700 "$PROBE"
install -m 0600 "$HERE/probe_server.py" "$PROBE/probe_server.py"
install -m 0600 "$HERE/probe_logic.py" "$PROBE/probe_logic.py"
install -m 0700 "$HERE/run_pg_tunnel.sh" "$PROBE/run_pg_tunnel.sh"
"$PY" -m py_compile "$PROBE/probe_server.py" "$PROBE/probe_logic.py"

cat > "$UNITS/$SVC1" <<'UNIT'
[Unit]
Description=Read-only PostgreSQL Secure MCP Tunnel proof server
After=network.target
[Service]
Type=simple
WorkingDirectory=/home/kali/hermes-pg-tunnel-readonly-probe
Environment=PYTHONPATH=/home/kali/bridge-pg-v2:/home/kali/hermes-pg-tunnel-readonly-probe
ExecStart=/home/kali/bridge-pg-v2/.venv/bin/python /home/kali/hermes-pg-tunnel-readonly-probe/probe_server.py
Restart=on-failure
RestartSec=4
UMask=0077
NoNewPrivileges=yes
UNIT
cat > "$UNITS/$SVC2" <<'UNIT'
[Unit]
Description=OpenAI Secure MCP Tunnel to read-only PG proof server
Wants=hermes-pg-readonly-probe.service
After=hermes-pg-readonly-probe.service network-online.target
[Service]
Type=simple
ExecStart=/home/kali/hermes-pg-tunnel-readonly-probe/run_pg_tunnel.sh
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
    echo 'PROBE_ACTIVATION_FAILED_ROLLING_BACK' >&2
    bash "$HERE/31_rollback.sh" || echo 'ROLLBACK_NEEDS_ATTENTION' >&2
  fi
}
trap on_exit EXIT

systemctl --user start "$SVC1"
for i in $(seq 1 40); do
  ping="$(curl -sS --noproxy '*' -o /dev/null -w '%{http_code}' --max-time 2 'http://127.0.0.1:18767/ping' 2>/dev/null || true)"
  [[ "$ping" == 200 ]] && break
  sleep 1
done
[[ "$ping" == 200 ]] || { echo STOP_local_probe_not_ready; exit 1; }
echo PG_READONLY_MCP_LOCAL_READY_200

# Exactly one runtime on the same logical OpenAI tunnel.
systemctl --user stop hermes-secure-tunnel-canary.service
[[ "$(systemctl --user is-active hermes-secure-tunnel-canary.service || true)" != active ]] || { echo STOP_old_stub_still_active; exit 1; }
systemctl --user start "$SVC2"
ready=000
for i in $(seq 1 50); do
  ready="$(curl -sS --noproxy '*' -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:18768/readyz 2>/dev/null || true)"
  [[ "$ready" == 200 ]] && break
  sleep 2
done
[[ "$ready" == 200 ]] || { echo STOP_tunnel_runtime_not_ready; exit 1; }
[[ "$(systemctl --user is-active "$SVC2")" == active ]] || { echo STOP_tunnel_not_active; exit 1; }
[[ "$(systemctl --user is-active bridge-pg-v2-mcp.service)" == active ]] || { echo STOP_original_mcp_changed; exit 1; }
[[ "$(systemctl --user is-active bridge-pg-v2-taskboard.service)" == active ]] || { echo STOP_original_taskboard_changed; exit 1; }
SUCCESS=1
trap - EXIT
echo PG_TUNNEL_REAL_DB_READONLY_READY_200
echo PG_TUNNEL_BACKEND_TOOLS=pg_health,pg_canary_receipt
echo OLD_DEMO_PAUSED_NOT_DELETED
