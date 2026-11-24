# Hermes PG v2 · Stage 35C · No domain, no OAuth, no Keycloak

Stage 35B verified 7 active plugins, a quiesced pilot and 42 role-bound SQL functions. This package creates a **separate inactive** Managed MCP service at `127.0.0.1:18777`, and a random local secret (0600) that will be used *only* in a later Secure MCP Tunnel cutover.

The active tunnel remains on `127.0.0.1:18773`; do not change it in this stage. The earlier inactive OAuth service at 18776 is left untouched. No SQL or plugin config modifications occur. No write permit is created.

## Operator tasks

After SHA256 check and extraction to `/home/kali/hermes-pg-stage35c-static-guard`, run 40 offline tests, Python/Bash syntax checks, then `117_readonly_preflight.sh`, `118_install_once_inactive.sh` **once only**, and `119_verify_only.sh`. Never print the secret or include it in reports.

If 118 exits with uncertain outcome, run only 119. Do not rerun installation. In a partial state, report which artifact is missing; ChatGPT will write a new recovery procedure.

## Planned *future* tunnel configuration (NOT in this package)

The official `tunnel-client` supports `MCP_EXTRA_HEADERS` with file-backed values. The next stage must first confirm the installed binary supports it, then configure its own environment/profile to apply:

- `MCP_SERVER_URL=http://127.0.0.1:18777/mcp` (actual tunnel-client option names are to be detected from the live unit; this is an illustrative target, not a command)
- `MCP_EXTRA_HEADERS=X-Bridge-Local-Key: file:/home/kali/.config/bridge-pg-v2/managed-mcp-local-key35c`
- `MCP_OAUTH_DISCOVERY=disabled`

The secret is injected on the *last local HTTP hop*, not forwarded from ChatGPT. The static backend header is not per-user identity. A connector-forwarded header can override a tunnel-client extra header, but cannot pass the server check without the secret. Always scope the tunnel to the owner's account/workspace, and keep the MCP listener on loopback.

Auth is required for ALL MCP requests. Unauthorized `tools/list`, `tools/call`, sessions, GET and DELETE are denied. All 11 tools are managed-only and run under `bridge_chatgpt`; the existing `bridge_agent` identity stays separate. Four write tools additionally require a separately created and task-bound **time-limited write permit**, which does not exist at this stage. No raw SQL or generic command execution tools are exposed.

## Recovery and safety

- The old working service, tunnel, and 7-plugin config stay live.
- The new service is explicitly inactive; no port 18777 listener expected.
- Secret is created only by the one-shot install, in the private `~/.config/bridge-pg-v2` directory.
- A local compromise of the user account `kali` may expose the secret and the existing PostgreSQL credentials. User isolation may be added later.
- Use the server-side role-based wrappers and exact message IDs for deduplication. Transport auth is not a replacement for policy enforcement.
