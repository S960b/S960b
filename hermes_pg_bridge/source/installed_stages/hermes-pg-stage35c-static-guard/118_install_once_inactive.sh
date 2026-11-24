#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
cd /home/kali/hermes-pg-stage35c-static-guard
bash 117_readonly_preflight.sh
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python stage35c.py install-once
echo STAGE35C_INSTALL_COMPLETE_NO_TUNNEL_OR_MCP_SWITCH
