#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
cd /home/kali/hermes-pg-stage35b-issuer-readiness
PY=/home/kali/bridge-pg-v2/.venv/bin/python
# Offline local assertion: missing issuer always blocks both discovery and writes.
env -u APPDIR -u APPIMAGE "$PY" - <<'PY'
import sys
sys.path.insert(0,'/home/kali/hermes-pg-stage35a-oauth-managed-shadow')
from oauth_guard35a import config_from_file,AuthDenied,required_for_body
try:
    config_from_file()
except AuthDenied:
    print('STAGE35B_SHADOW_OAUTH_FAIL_CLOSED_CONFIG_ABSENT')
else:
    raise SystemExit('STAGE35B_STOP_OAUTH_CONFIG_UNEXPECTEDLY_PRESENT')
assert required_for_body(b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}')=='managed:read'
assert required_for_body(b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"managed_send_task"}}')=='managed:write'
print('STAGE35B_MCP_READ_WRITE_SCOPES_DISTINCT')
PY
# Full 35A test suite runs entirely with generated test keys and local test ASGI.
cd /home/kali/hermes-pg-stage35a-oauth-managed-shadow
env -u APPDIR -u APPIMAGE "$PY" -m unittest -q test_stage35a
cd /home/kali/hermes-pg-stage35b-issuer-readiness
env -u APPDIR -u APPIMAGE "$PY" metadata35b.py
printf 'STAGE35B_STAGE35A_OFFLINE_AUTH_TESTS_PASSED\n'
