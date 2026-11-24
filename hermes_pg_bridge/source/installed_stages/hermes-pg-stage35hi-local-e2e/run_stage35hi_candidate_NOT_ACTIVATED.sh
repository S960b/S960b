#!/usr/bin/env bash
# CANDIDATE ONLY; this stage NEVER starts this file.
# Future deployment requires a separately audited cutover+rollback stage.
set -Eeuo pipefail
set +x
umask 077
ulimit -c 0
[[ ${STAGE35HI_EXPLICITLY_ARMED_BY_FUTURE_PACKAGE:-} == YES_WITH_SEPARATE_ROLLBACK ]] || {
  printf 'STAGE35I_CANDIDATE_BLOCKED_NOT_ARMED\n' >&2; exit 2;
}
[[ $(id -un) == kali && $HOME == /home/kali ]] || exit 2
KEY=/home/kali/.config/hermes-secure-tunnel-canary/runtime.key
LOCAL_KEY=/home/kali/.config/bridge-pg-v2/managed-mcp-local-key35c
BIN=/home/kali/.local/libexec/hermes-secure-tunnel-canary/v0.0.21/tunnel-client
[[ -f $KEY && ! -L $KEY && $(stat -c %a "$KEY") == 600 &&
   -f $LOCAL_KEY && ! -L $LOCAL_KEY && $(stat -c %a "$LOCAL_KEY") == 600 &&
   -x $BIN ]] || exit 2
# Existing run launcher keeps its same runtime key and tunnel ID.
export CONTROL_PLANE_API_KEY="$(head -n 1 "$KEY")"
[[ -n $CONTROL_PLANE_API_KEY ]] || exit 2
export CONTROL_PLANE_TUNNEL_ID='tunnel_6b02fe0b07f4819194b5adf2ff5e6a6a'
export MCP_SERVER_URL='http://127.0.0.1:18777/mcp'
export MCP_OAUTH_DISCOVERY='disabled'
export MCP_EXTRA_HEADERS='X-Bridge-Local-Key: file:/home/kali/.config/bridge-pg-v2/managed-mcp-local-key35c'
# Extra headers contain the *file reference*, never the file's contents.
exec "$BIN" run --health.listen-addr 127.0.0.1:18778
