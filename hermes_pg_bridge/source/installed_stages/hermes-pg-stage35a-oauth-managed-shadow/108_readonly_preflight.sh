#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
cd /home/kali/hermes-pg-stage35a-oauth-managed-shadow
PY=/home/kali/bridge-pg-v2/.venv/bin/python
[[ "$(id -un)" == kali && "$HOME" == /home/kali ]] || exit 2
[[ ! -e /home/kali/.config/systemd/user/hermes-pg-stage35a-oauth-shadow.service ]] || { echo STAGE35A_STOP_UNIT_EXISTS >&2;exit 2; }
env -u APPDIR -u APPIMAGE "$PY" -c 'import mcp, jwt, cryptography, starlette, uvicorn, psycopg'
env -u APPDIR -u APPIMAGE "$PY" -c 'import managed_mcp35a; assert callable(managed_mcp35a.create_app())'
env -u APPDIR -u APPIMAGE "$PY" stage35a.py preflight
echo STAGE35A_PREFLIGHT_COMPLETE_NO_WRITES
