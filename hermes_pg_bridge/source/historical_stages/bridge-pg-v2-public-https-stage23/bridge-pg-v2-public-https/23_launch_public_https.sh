#!/usr/bin/env bash
# Authored by ChatGPT. One-shot controlled, temporary HTTPS for PG MCP only.
set -Eeuo pipefail
umask 077
[[ "$(id -un)" == kali && "$HOME" == /home/kali ]] || { echo WRONG_USER; exit 2; }
[[ -f /home/kali/bridge-pg-v2/bridge_pg/mcp_http_v2.py ]] || { echo MCP_V2_MISSING; exit 2; }
[[ -x /home/kali/bridge-pg-v2/.venv/bin/python ]] || { echo VENV_MISSING; exit 2; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
/home/kali/bridge-pg-v2/.venv/bin/python "$HERE/23_public_https.py"
