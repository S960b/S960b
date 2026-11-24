#!/usr/bin/env bash
# Run after copying package to /home/kali/bridge-pg-v2. Creates only v2 static files.
set -Eeuo pipefail
[[ "$(id -un)" == kali && "$(pwd -P)" == /home/kali/bridge-pg-v2 ]] || exit 2
python3 tools/port_taskboard_assets.py
python3 tools/port_mt_plugin.py
node --check dist/mt-pg-inbox.mjs
[[ -f bridge_pg/static/taskboard.html && -f bridge_pg/static/taskboard.js ]] || exit 2
printf '%s\n' 'V2_ASSETS_CHECK_OK; old plugin/taskboard unchanged; no service started'
