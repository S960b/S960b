#!/usr/bin/env bash
# Dedicated tunnel runtime; NEVER log an API key.
set -Eeuo pipefail
set +x
umask 077
ulimit -c 0
[[ "$(id -un)" == kali ]] || exit 2
KEY=/home/kali/.config/hermes-secure-tunnel-canary/runtime.key
BIN=/home/kali/.local/libexec/hermes-secure-tunnel-canary/v0.0.21/tunnel-client
[[ -f "$KEY" && ! -L "$KEY" && "$(stat -c %a "$KEY")" == 600 && -x "$BIN" ]] || exit 2
export CONTROL_PLANE_API_KEY="$(head -n 1 "$KEY")"
export CONTROL_PLANE_TUNNEL_ID='tunnel_6b02fe0b07f4819194b5adf2ff5e6a6a'
export MCP_SERVER_URL='http://127.0.0.1:18767/mcp'
export MCP_OAUTH_DISCOVERY='disabled'
[[ -n "$CONTROL_PLANE_API_KEY" ]] || exit 2
exec "$BIN" run --health.listen-addr 127.0.0.1:18768
