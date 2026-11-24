#!/usr/bin/env bash
# Read-only operational and DB preflight for the bounded write canary.
set -Eeuo pipefail
set +x
umask 077
[[ "$(id -un)" == kali && "$HOME" == /home/kali ]] || { echo STOP_wrong_user; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd -P)"
BASE=/home/kali/bridge-pg-v2
PY="$BASE/.venv/bin/python"
[[ -x "$PY" && -f "$BASE/bridge_pg/compat.py" ]] || { echo STOP_bridge_python_missing; exit 2; }
[[ "$(systemctl --user is-active bridge-pg-v2-mcp.service)" == active ]] || { echo STOP_existing_mcp_inactive; exit 2; }
[[ "$(systemctl --user is-active bridge-pg-v2-taskboard.service)" == active ]] || { echo STOP_taskboard_inactive; exit 2; }
[[ "$(systemctl --user is-active hermes-pg-tunnel-probe.service)" == active ]] || { echo STOP_readonly_tunnel_inactive; exit 2; }
[[ "$(systemctl --user is-active hermes-pg-readonly-probe.service)" == active ]] || { echo STOP_readonly_server_inactive; exit 2; }
[[ "$(systemctl --user is-active hermes-secure-tunnel-canary.service || true)" != active ]] || { echo STOP_demo_tunnel_conflict; exit 2; }
[[ -s /home/kali/hermes-pg-tunnel-readonly-probe/run_pg_tunnel.sh ]] || { echo STOP_previous_tunnel_launcher_missing; exit 2; }
for p in 18769 18770; do
  if ss -ltn "( sport = :$p )" | tail -n +2 | grep -q .; then
    echo "STOP_new_port_busy_$p"; exit 2
  fi
done
[[ ! -e /home/kali/hermes-pg-tunnel-one-shot-canary ]] || { echo STOP_previous_canary_directory_exists; exit 2; }
[[ ! -e /home/kali/.config/systemd/user/hermes-pg-one-shot-canary.service ]] || { echo STOP_new_server_unit_exists; exit 2; }
[[ ! -e /home/kali/.config/systemd/user/hermes-pg-tunnel-one-shot.service ]] || { echo STOP_new_tunnel_unit_exists; exit 2; }
PYTHONPATH="$HERE:$BASE" "$PY" - <<'PY'
from pathlib import Path
import json
from bridge_pg.compat import Catalog
from logic31 import CANARY_ID, start_state
from bridge_pg.store import Store
assert Store().health().get('ok') is True, 'postgres_not_ready'
v=Catalog().view(CANARY_ID, 0, 5, 'newest')
assert start_state(v), 'canary_state_not_expected_2msg_waiting_chatgpt'
conf=Path('/home/kali/.config/gigatool/opencode.json')
config=json.loads(conf.read_text())
plugins=config.get('plugin')
assert isinstance(plugins,list), 'plugin_list_missing'
canaries=[]
for entry in plugins:
    if isinstance(entry,list) and len(entry)==2 and isinstance(entry[0],str) and isinstance(entry[1],dict):
        path, opts = entry
        if 'mt-pg-inbox-canary.mjs' in path: canaries.append(opts)
assert len(canaries)==1, 'canary_plugin_ambiguous'
assert canaries[0].get('dry_run') is False, 'canary_not_live'
assert canaries[0].get('task_ids') == [CANARY_ID], 'canary_task_filter_changed'
source=Path('/home/kali/bridge-pg-v2/dist/mt-pg-inbox-canary.mjs').read_text()
assert 'Object.freeze(["'+CANARY_ID+'"]);' in source, 'hardcoded_task_filter_changed'
print('CANARY_DB_TURN_AND_AGENT_FILTER_VERIFIED')
print('EXISTING_PLUGINS_NOT_MODIFIED')
PY
code="$(curl --noproxy '*' -fsS --max-time 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:18768/readyz)"
[[ "$code" == 200 ]] || { echo STOP_old_tunnel_runtime_not_ready; exit 2; }
echo STAGE31_READ_ONLY_PREFLIGHT_OK
