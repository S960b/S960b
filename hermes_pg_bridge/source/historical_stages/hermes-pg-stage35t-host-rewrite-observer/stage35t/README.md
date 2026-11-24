# Stage 35T — MCP 421 authority fix + route-specific observer

ChatGPT-authored, one-shot, private localhost temporary observer. Does NOT implement OAuth, touch PostgreSQL, or change local MCP/auth secret. Uses original tunnel ID. Previous 35S must be fully restored.

## Root cause targeted

35S observed an `initialize` request with **matching local key** but got `HTTP 421` during the local preflight; two `auth=missing` requests were `/ping`, not MCP. Therefore 35S did not prove missing auth on ChatGPT traffic. The original observer forwarded `Host: 127.0.0.1:18779` to an MCP origin on 18777, potentially triggering host protection.

35T rewrites only `Host` to the actual loopback MCP origin and makes the upstream connection one-request (`Connection: close`). Request body, auth header, Accept, MCP session and method remain unchanged. No secret values, headers or bodies are logged. These modifications are only on the temporary diagnostic observer; it is not a production proxy.

## Operator workflow

1. Verify outer zip checksum and run `250_install_and_activate_once.sh` exactly once as `kali`. It checks 35S restoration, source hashes, one tunnel, 7 plugins, no DB write permit, then starts temporary observer and preflights real MCP `initialize` and `tools/list` through it. Before redirecting tunnel, it arms systemd 12-minute restore timer. A failure before redirect stops observer without restarting healthy tunnel.
2. ONLY if `STAGE35T_NOW_ATTEMPT_CHATGPT_PLUGIN_CREATION_ONCE_THEN_COLLECT` appears, ask user to attempt creating MCP plugin with existing tunnel + No authentication once. If that marker does not appear, do NOT create the plugin.
3. Immediately after the attempt, run `/home/kali/hermes-pg-stage35t-connector-observer/251_collect_restore.sh`; report only STAGE35T markers. Automatic restore attempts after 12 minutes regardless. At any failure, do not rerun activate.
4. Manual `252_emergency_restore.sh` only on explicit instruction or documented break-glass necessity.

## Interpret results

`MCP_AUTH_MISSING` and `MCP_AUTH_MATCH` count ONLY `/mcp` traffic, not `/ping`; a health probe without key is normal. The observer records only the first request on each TCP connection and a fixed small whitelist of metadata: route, RPC method category, auth match/missing, HTTP status. Host header values are never logged or echoed. HTTP 421 must be zero on real MCP preflight. The remote ChatGPT error can still originate in the tunnel/control-plane before reaching observer: absence of `/mcp` requests points there, not to auth failure at MCP.

**Security:** Private files 0600, units under owner-only systemd directory, no public port, no secrets in stdout or logs. One-shot markers and strict rollback. Do not manually bypass source pins.
