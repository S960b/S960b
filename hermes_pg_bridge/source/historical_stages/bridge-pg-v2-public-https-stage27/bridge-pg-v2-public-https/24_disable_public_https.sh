#!/usr/bin/env bash
# Explicit rollback from temporary public test to localhost-only MCP.
set -Eeuo pipefail
umask 077
[[ $(id -un) == kali && $HOME == /home/kali ]] || { echo WRONG_USER; exit 2; }
CFG=/home/kali/.config/bridge-pg-v2
UNITDIR=/home/kali/.config/systemd/user
URLFILE="$CFG/quick-tunnel-url"
DROPIN="$UNITDIR/bridge-pg-v2-mcp.service.d/20-quick-tunnel-base.conf"
QUICK="$UNITDIR/bridge-pg-v2-quick-tunnel.service"
V2CONF="$CFG/quick-tunnel-isolated.yml"
[[ -f "$URLFILE" && ! -L "$URLFILE" && -f "$DROPIN" && ! -L "$DROPIN" && -f "$QUICK" && ! -L "$QUICK" && -f "$V2CONF" && ! -L "$V2CONF" ]] || { echo TEST_CONFIG_INCOMPLETE_REFUSE_REMOVAL; exit 2; }
URL="$(cat "$URLFILE")"
[[ "$URL" =~ ^https://[a-z0-9-]+\.trycloudflare\.com$ ]] || { echo TEMP_URL_MISMATCH; exit 2; }
EXPECTED="$(printf '[Service]\nEnvironment=PG_V2_MCP_BASE=%s\n' "$URL")"
[[ "$(cat "$DROPIN")" == "$EXPECTED" ]] || { echo MCP_DROPIN_CHANGED_STOP; exit 2; }
python3 - "$V2CONF" <<'PYTEST'
from pathlib import Path
import sys
p=Path(sys.argv[1])
assert p.read_bytes() == b'no-autoupdate: true\ningress:\n  - service: http://127.0.0.1:8766\n', 'ISOLATED_CONFIG_CHANGED_STOP'
PYTEST
# Stop the public edge FIRST, before restoring the local-only MCP OAuth base.
systemctl --user stop bridge-pg-v2-quick-tunnel.service
rm -- "$DROPIN" "$QUICK" "$URLFILE" "$V2CONF"
systemctl --user daemon-reload
systemctl --user restart bridge-pg-v2-mcp.service
/home/kali/bridge-pg-v2/.venv/bin/python - <<'PY'
import json,time,urllib.request
for _ in range(20):
  try:
    with urllib.request.urlopen('http://127.0.0.1:8766/.well-known/oauth-authorization-server',timeout=3) as response:
      assert response.status==200
      assert json.load(response)['issuer']=='http://127.0.0.1:8766'
    break
  except (OSError,AssertionError,ValueError):time.sleep(.5)
else:raise SystemExit('MCP_NOT_RESTORED_LOCAL_ONLY')
print('TEMP_HTTPS_REMOVED_MCP_LOOPBACK_ONLY_OK')
PY
