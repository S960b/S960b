#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
cd /home/kali/hermes-pg-stage35c-static-guard
PY=/home/kali/bridge-pg-v2/.venv/bin/python
test "$(id -un)" = kali
env -u APPDIR -u APPIMAGE "$PY" -c 'import mcp, starlette, uvicorn, psycopg;import managed_mcp35c;assert callable(managed_mcp35c.create_app())'
env -u APPDIR -u APPIMAGE "$PY" stage35c.py preflight
