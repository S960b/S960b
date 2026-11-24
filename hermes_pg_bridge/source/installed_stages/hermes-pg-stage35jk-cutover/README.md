# Stage 35J + 35K — pinned dev-proxy diagnostic and rollback-guarded cutover

Written by ChatGPT. Agent executes exact provided commands; no self-directed edits.

## Already confirmed in 35H

- Real tunnel-client v0.0.21 injects `X-Bridge-Local-Key` from `file:/home/kali/.config/bridge-pg-v2/managed-mcp-local-key35c`.
- `initialize`, exact 11 MCP tools, `managed_health`, `managed_list`, role `bridge_chatgpt`, pilot disabled, no DB writes, successful SIGTERM of both services.
- Previous **session DELETE failure reason unknown**. `proxy35hi.py` already accepted 200/202/204/404/405, so accepting those codes again is not an actual fix.

## One-command procedure

`bash 180_install_verify_and_cutover_once.sh`

1. SHA256 file verification + offline regression suite; isolated new directory install.
2. Read-only pins, seven-plugin config fingerprint, old tunnel /ping, old systemd unit, original 35G service/drop-in and code.
3. **Stage 35J local-only**: patched 35C service + installed real `tunnel-client dev proxy`, authenticated file-backed header, exact 11 tools, read-only role-bound DB data. DELETE logged as `LOCAL_SESSION_DELETE_HTTP_###` or `NETWORK_...`; unexpected DELETE results are **not concealed** but do not cancel the proven RPCs. Both processes must terminate normally by SIGTERM.
4. **Stage 35K candidate cutover**: gated on 35J, starts 35C service, verifies `18777/ping`, writes a new unenabled systemd unit, stops old tunnel first (same tunnel ID), starts new tunnel on `18777/mcp`, validates `127.0.0.1:18778/readyz`. Verifies old tunnel inactive and all seven plugins unchanged.
5. **Automatic rollback** on failure: stop NEW tunnel, confirm its health port closes, start OLD tunnel (`18774/readyz`), then stop managed shadow. If old cannot be restored, emit CRITICAL and stop, no false success.

**Remote ChatGPT plugin call is still required** after cutover. Local `/readyz` proves readiness, not remote ChatGPT-to-MCP operation. Never arm managed dispatch or allow DB writes in this stage.

## Explicit recovery scripts

- `bash 181_verify_only.sh` — status check, non-mutating, repeatable.
- `bash 182_rollback_if_needed.sh` — **ONLY** when authorized, e.g. remote ChatGPT plugin fails. Stops new tunnel before restarting old. Does not overwrite original tunnel files.

## Security

- No API key printed. Existing production runtime key and tunnel ID preserved, used by new unit after stopping old.
- Neither PostgreSQL writes nor seven-plugin edits nor old bridge code changes.
- No installation overwrite, one-shot markers before service mutation.
- Logs in installed private directory; **do not paste** raw logs or environment variables.
- The 35C systemd unit retains `Restart=no` from 35G; new tunnel service has `Restart=on-failure` but is not enabled at boot. Longer-term persistence requires explicit follow-up after real remote plugin validation.

## Success markers

`STAGE35JK_LOCAL_E2E_PROVEN_OLD_TUNNEL_UNCHANGED`
`STAGE35JK_CUTOVER_NEW_SINGLE_TUNNEL_READY_18778_OLD_TUNNEL_INACTIVE`
`STAGE35JK_CUTOVER_COMPLETE_LOCAL_HEALTH_OK_REMOTE_PLUGIN_CHECK_PENDING`

If cleanup shows a warning, include only the safe status markers, not raw logs.
