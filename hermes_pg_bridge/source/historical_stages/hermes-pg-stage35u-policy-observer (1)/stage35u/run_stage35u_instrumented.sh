#!/usr/bin/env bash
# Stage35K protected production candidate; only started by exact systemd unit.
set -Eeuo pipefail
set +x
umask 077
ulimit -c 0
[[ $(id -un) == kali && "$HOME" == /home/kali && ${EUID} != 0 ]] || exit 2
ROOT=/home/kali/hermes-pg-stage35jk-cutover
[[ -f "$ROOT/stage35k-cutover-attempt.json" && ! -L "$ROOT/stage35k-cutover-attempt.json" ]] || exit 2
KEY=/home/kali/.config/hermes-secure-tunnel-canary/runtime.key
LOCAL_KEY=/home/kali/.config/bridge-pg-v2/managed-mcp-local-key35c
BIN=/home/kali/.local/libexec/hermes-secure-tunnel-canary/v0.0.21/tunnel-client
for secret in "$KEY" "$LOCAL_KEY"; do
  [[ -f $secret && ! -L $secret && $(stat -c %u "$secret") == $(id -u) &&
     $(stat -c %a "$secret") == 600 && $(stat -c %h "$secret") == 1 ]] || exit 2
done
[[ -x "$BIN" && ! -L "$BIN" ]] || exit 2
export CONTROL_PLANE_API_KEY="$(head -n 1 "$KEY")"
[[ -n "$CONTROL_PLANE_API_KEY" ]] || exit 2
export CONTROL_PLANE_TUNNEL_ID='tunnel_6b02fe0b07f4819194b5adf2ff5e6a6a'
export MCP_SERVER_URL='http://127.0.0.1:18779/mcp'
export MCP_OAUTH_DISCOVERY='disabled'
export MCP_EXTRA_HEADERS='X-Bridge-Local-Key: file:/home/kali/.config/bridge-pg-v2/managed-mcp-local-key35c'
# Existing identity preserved. Never send credential value through stdout.
exec "$BIN" run --health.listen-addr 127.0.0.1:18778
