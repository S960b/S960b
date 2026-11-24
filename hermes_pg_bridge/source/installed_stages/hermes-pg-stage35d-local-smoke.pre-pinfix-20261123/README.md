# Hermes PG v2 — Stage 35D — Local Auth Smoke, **no tunnel cutover**

This is a *new separate stage*. Stage 35C secret is already installed. We test the installed OpenAI `tunnel-client` binary version 0.0.21 *before* doing anything, then start the unregistered MCP on **loopback 127.0.0.1:18777** without a write permit and run a real MCP initialize → tools/list → health/list read-only roundtrip with a private local secret. No Git bridge, SQL writes, SDK execution, tunnel modification, app registration or DNS.

The existing connected tunnel keeps pointing at `127.0.0.1:18773/mcp`; the seven-plugin config is pinned by SHA256. The prior inactive OAuth shadow on 18776 remains inactive.

The official Secure MCP Tunnel docs say static HTTP headers accept `file:/path/to/secret` values in `MCP_EXTRA_HEADERS` / `--mcp.extra-headers`, and are sent only to the MCP origin. **We do not assume the locally installed binary supports it**: `stage35d.py preflight` runs `tunnel-client run --help` (without any credentials) and requires the `--mcp.extra-headers` flag. If unsupported, the stage stops *before* starting the new MCP, with marker `STAGE35D_STOP_INSTALLED_CLIENT_LACKS_MCP_EXTRA_HEADERS_FLAG`. Report that marker; do not upgrade the binary or introduce a proxy without a separate ChatGPT-produced package.

## Operator (user kali)

Extract to `/home/kali/hermes-pg-stage35d-local-smoke`. Verify ZIP SHA256 and contents `SHA256SUMS`. Run `python -m unittest -v test_stage35d`, `python -m py_compile stage35d.py smoke35d.py test_stage35d.py`, `bash -n *.sh`.

1. `bash 120_readonly_preflight.sh` verifies the existing tunnel, config, local secret, no write permit and installed binary capability. No mutations.
2. `bash 121_start_local_shadow_once.sh` starts only `hermes-pg-stage35c-static-shadow.service` and smoke-tests the local MCP with header injection performed by this local test script. The script journals the attempt and never retries automatically.
3. `bash 122_verify_only.sh` checks read-only local MCP and that old tunnel is unchanged.

**Don't call any write tool**. `smoke35d.py` intentionally invokes only `managed_health` and `managed_list`. MCP advertises all 11 managed tools but cannot write without a separately issued local write permit. Not having a write permit is not a replacement for local service authentication.

Local service may remain running between 35D and 35E, but is not enabled at boot and is **not connected to the tunnel**. If a smoke test fails, `121_start_local_shadow_once.sh` stops the local service, retaining the immutable attempt marker. `123_stop_local_shadow.sh` may be explicitly run to stop only the shadow; never use it to manipulate the production tunnel.

## 35E planned, not performed

Only after the report confirms binary header support and live local MCP auth do we prepare a new tunnel launcher with `MCP_SERVER_URL=http://127.0.0.1:18777/mcp`, `MCP_EXTRA_HEADERS='X-Bridge-Local-Key: file:/home/kali/.config/bridge-pg-v2/managed-mcp-local-key35c'` and disabled OAuth discovery, and an exclusive service handoff / rollback. The header value must remain private and never appear in logs. We must separately prove that the **tunnel client itself** performs this injection, because the current stage performs the injection only in its isolated local smoke test. The new MCP's write permit must stay absent during tunnel cutover.

No user/ChatGPT OAuth needed for this private test, but static header authenticates the tunnel, not an end user. Keep the app personal and confirm tunnel/workspace access controls before enabling write tools.
