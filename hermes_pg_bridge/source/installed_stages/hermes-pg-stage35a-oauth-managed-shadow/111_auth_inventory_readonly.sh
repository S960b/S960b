#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
cd /home/kali/hermes-pg-stage35a-oauth-managed-shadow
PY=/home/kali/bridge-pg-v2/.venv/bin/python
env -u APPDIR -u APPIMAGE "$PY" stage35a.py auth-audit
# Show only whether OAuth discovery is enabled, never auth secrets or env.
BIN=/home/kali/.local/libexec/hermes-secure-tunnel-canary/v0.0.21/tunnel-client
if [[ -x "$BIN" ]]; then
  echo STAGE35A_TUNNEL_CLIENT_PRESENT
else
  echo STAGE35A_TUNNEL_CLIENT_UNAVAILABLE
fi
# The active tunnel's run script is pinned to the existing canary and must remain unchanged.
if grep -q "MCP_OAUTH_DISCOVERY='disabled'" /home/kali/hermes-pg-stage33fd-tunnel-cutover/run_stage33fd_tunnel.sh; then
  echo STAGE35A_EXISTING_TUNNEL_OAUTH_DISCOVERY_DISABLED
else
  echo STAGE35A_EXISTING_TUNNEL_OAUTH_DISCOVERY_NOT_CONFIRMED
fi
echo STAGE35A_OAUTH_AUDIT_COMPLETE_NO_SECRETS
