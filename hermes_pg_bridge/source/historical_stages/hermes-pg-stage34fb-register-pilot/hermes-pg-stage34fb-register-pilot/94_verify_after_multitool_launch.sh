#!/usr/bin/env bash
# Read-only logging check AFTER owner reopens one MultiTool instance.
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34fb-register-pilot
PY=/home/kali/bridge-pg-v2/.venv/bin/python
cd "$ROOT"
env -u APPDIR -u APPIMAGE "$PY" handoff34fb.py verify
LOG=/home/kali/.local/state/mt-pg-inbox-managed-stage34e/inbox.log
[[ -f "$LOG" && ! -L "$LOG" ]] || { echo STAGE34FB_PLUGIN_LOG_NOT_YET_CREATED; exit 2; }
# Inspect static event classes only. Do not copy log body or secrets.
"$PY" - "$LOG" <<'PY'
import sys
from pathlib import Path
p=Path(sys.argv[1]); lines=p.read_text('utf8',errors='replace').splitlines()[-120:]
loaded=any('plugin loaded;' in s for s in lines)
idle=any('tick_ok ' in s for s in lines)
print('STAGE34FB_MANAGED_PLUGIN_LOADED=' + str(loaded).lower())
print('STAGE34FB_MANAGED_PLUGIN_IDLE_TICK=' + str(idle).lower())
if not loaded or not idle:
    raise SystemExit(2)
print('STAGE34FB_MANAGED_PLUGIN_LOADED_IDLE_NO_DISPATCH')
PY
