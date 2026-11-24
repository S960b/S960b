#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
[[ $(id -un) == kali ]] || exit 2
systemctl --user is-active bridge-pg-v2-mcp.service
systemctl --user is-active bridge-pg-v2-quick-tunnel.service
systemctl --user is-enabled bridge-pg-v2-quick-tunnel.service 2>/dev/null || true
/home/kali/bridge-pg-v2/.venv/bin/python - <<'PY'
from pathlib import Path
import urllib.request,json
p=Path('/home/kali/.config/bridge-pg-v2/quick-tunnel-url')
assert p.is_file() and not p.is_symlink()
u=p.read_text().strip()
assert u.startswith('https://') and u.endswith('.trycloudflare.com')
with urllib.request.urlopen(u+'/ping',timeout=6) as r:
    assert r.status==200 and json.load(r)['service']=='pg-bridge-v2'
print('TEMPORARY_PUBLIC_HTTPS_STILL_ACTIVE')
print('PUBLIC_MCP_URL='+u+'/mcp')
PY
