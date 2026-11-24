# Hermes PostgreSQL Bridge v2 — Stage 35A: OAuth-managed MCP shadow

**Status:** package prepared for a read-only installation of an **inactive** local service. **Not a production cutover.** No SQL migration, no changes to current 7-plugin MultiTool config, no activation of managed tasks, no Git, no SDK call, no active tunnel modification. Installed service remains `inactive` and `disabled`.

## Exact baseline (from Stage34G report)

- Kali user `kali`, persistent PostgreSQL shadow SSH forward 127.0.0.1:15433 to VPS.
- PostgreSQL DB `bridge`, schema 4 tables and 42 functions, `bridge_chatgpt` and `bridge_agent` real separate logins.
- Canary `task_f2c64cfdf7894537b35a19a6def977c6`: last_seq=6, deliveries 5/6 delivered.
- Managed pilot `task_bddaadef89b7458fb7f8cf437dc0f023`: policy_version=2, dispatch off, version/seq=2, deliveries 1/2 delivered. One managed dialogue in registry.
- `opencode.json` 7 plugins, SHA256 `e5320f6e971073d5d2c43a64ce85d165cb49dc9a94e6cf7806009ebc2b59ded1`.
- Existing Secure Tunnel continues to point to **127.0.0.1:18773/mcp**, stage33fb fixed canary; its OAuth discovery is currently **disabled**.

## Design

Stage35A introduces a **separate MCP candidate** on `127.0.0.1:18776/mcp` (service `hermes-pg-stage35a-oauth-shadow.service`), not connected to the tunnel. It offers managed-only tools:

| Read-only tool | Purpose |
|---|---|
| `managed_health` | PG role-bound health and managed count |
| `managed_list` | Bounded list of registered dialogues |
| `managed_status` | Policy, versions and actor |
| `managed_read` | Bounded message history, including reports |
| `managed_delivery_status` | Receipt by seq |
| `managed_create_status` | Idempotency lookup after unknown create outcome |
| `managed_send_status` | Idempotency lookup after unknown send outcome |

| Write tool (not armed) | Purpose |
|---|---|
| `managed_create` | Create new **disabled** managed dialogue |
| `managed_set_dispatch` | Version-checked policy; enable additionally requires local permit |
| `managed_send_task` | Versioned, idempotent task; local permit + DB policy required |
| `managed_ack_report` | Verify exact report, SHA256 and atomically claim/ack using `bridge_chatgpt` (lease token never exposed) |

**Critical:** NO `managed-mcp-write-permit35b.json` is shipped; `managed_create`, `managed_send_task` and enabling dispatch are **always denied** in this stage. The other write tools still require OAuth `managed:write` authorization; since no valid OAuth issuer/JWKS configuration is shipped, **all `/mcp` requests (even discovery and reads) are denied**. `/ping` is intentionally unprivileged and reports only a generic service name, not authorization readiness.

The MCP server never opens a direct table privilege. It uses the role-bound SQL wrappers. The legacy `bridge_app` role is not used; do not revoke it in this stage. The current SSH shadow and actor credentials remain unchanged.

## Authentication: actual requirement, not inferred

**Secure MCP Tunnel is transport, not OAuth identity.** The current tunnel server 18773 has no OAuth, which was acceptable for the immutable canary but unsafe for general writes. The new gateway includes an outer ASGI OAuth JWT resource-server middleware that rejects unsigned tokens, any algorithm except RS256, unknown JWK IDs, bad issuer/audience, unauthorized `sub`, missing scope, expired tokens and lifetimes >1 hour. Tool listing, SSE GET, and all MCP requests require `managed:read` or `managed:write` scope; tool-call authorization is checked on the HTTP JSON-RPC body **before** SDK dispatch. No DB credential or local permit is a substitute for authenticating ChatGPT's calling user.

The future **OAuth authorization server is NOT provided or fabricated in this package**. Before any tunnel cutover, provide a reviewed, reachable issuer, correct audience/resource URI, and issuer's PUBLIC JWKS. Implementing an OAuth authorization server ourselves or assuming the legacy 8766 source proves a working OAuth flow would be unsafe. The `/.well-known/oauth-protected-resource/mcp` route returns metadata only when a complete validated config is present. It is not a token-issuing endpoint.

Example of a **future**, NOT installed, configuration (all placeholders; do not fill without verified issuer metadata):

```json
{
  "issuer": "https://VERIFIED-IDP-ISSUER.example/",
  "audience": "https://VERIFIED-MCP-RESOURCE.example/mcp",
  "resource": "https://VERIFIED-MCP-RESOURCE.example/mcp",
  "subjects": ["VERIFIED-ACCOUNT-SUBJECT"],
  "jwks_file": "/home/kali/.config/bridge-pg-v2/issuer-public-jwks.json"
}
```

Intended path: `/home/kali/.config/bridge-pg-v2/managed-oauth35a.json` (0600) with an owner-owned **public JWKS** file (0600). The installed default is deliberately **missing**, so all MCP access fails closed. Private OAuth signing keys are never requested or copied. If the actual issuer issues opaque/non-RS256 tokens or an OAuth flow needs other verification, stop and redesign authentication; do NOT disable the guard.

For OAuth via Tunnel, the authorization endpoint must be available to the browser/OAuth client; discovery may travel through the tunnel, but that does not automatically make a private authorization server public. Furthermore, the currently active `run_stage33fd_tunnel.sh` has `MCP_OAUTH_DISCOVERY='disabled'` — a future cutover stage must review and change that explicitly, after independent OAuth testing. References:
- https://developers.openai.com/api/docs/guides/secure-mcp-tunnels
- https://github.com/openai/tunnel-client/blob/master/docs/architecture.md
- https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization

## Install procedure (agent executes exact code only)

Extract to `/home/kali/hermes-pg-stage35a-oauth-managed-shadow` after SHA verification, run offline tests, `python -m py_compile`, `bash -n`, then execute:

```
bash 108_readonly_preflight.sh
bash 109_install_inactive_once.sh
bash 110_verify_only.sh
bash 111_auth_inventory_readonly.sh
```

All preflight/verification paths are read-only (except `109_...` which creates exactly one inactive systemd unit). `109_...` must be invoked **only once**. On unknown result, run `110_verify_only.sh` only. Do not `systemctl start` or `enable`, do not create any OAuth config/permit, do not change `run_stage33fd_tunnel.sh` and do not set `MCP_SERVER_URL` to 18776. No access tokens or DSN should appear in the report.

The preflight verifies that the installed venv already supports `mcp`, `jwt` (PyJWT), `cryptography`, `starlette`, `uvicorn` and `psycopg`. If an import fails, stop and report its **module name only**; do not update the working production venv ad hoc. A later package can add isolated, pinned dependencies.

## Follow-on requirements (Stage 35B and beyond)

1. Independently identify the REAL OAuth issuer and configuration used by old MCP 8766 (`require_owner()` in source is not sufficient proof). Audit discovery, callback accessibility, JWKS/scope and actual ChatGPT connector UI, without printing secrets.
2. Test authenticated MCP discovery and read-only tool calls through a **separate new tunnel**, not by replacing active 18773. Test no token, wrong user, expired token, insufficient scope, and valid owner token. Preserve fail-closed behavior.
3. Provide a time-bounded, exact-task write permit after approved OAuth path and re-registration of a managed MT executor; verify a fresh managed full roundtrip, including report ACK.
4. Only then perform exclusive tunnel cutover. Later move to VPS and separate UNIX users/credentials; retire legacy `bridge_app` and quick-tunnel only after a stable observation period.

## Markers

- `STAGE35A_REAL_TWO_ROLES_42_FUNCTIONS_ONE_DISABLED_MANAGED_PILOT`
- `STAGE35A_CANARY_AND_BOTH_PILOT_ACKS_DELIVERED`
- `STAGE35A_NO_AGENT_GATE_NO_MCP_WRITE_PERMIT_DISPATCH_EMPTY`
- `STAGE35A_READ_ONLY_PREFLIGHT_PASSED`
- `STAGE35A_EXISTING_SEVEN_PLUGIN_AND_TUNNEL_18773_PINNED`
- `STAGE35A_OAUTH_SHADOW_SYSTEMD_UNIT_CREATED_INACTIVE`
- `STAGE35A_SHADOW_UNIT_INSTALLED_INACTIVE_NO_TUNNEL_SWITCH`
- `STAGE35A_AUTH_UNCONFIGURED_FAIL_CLOSED`
- `STAGE35A_READ_ONLY_VERIFY_SUCCESS`
- `STAGE35A_OAUTH_CONFIG_MISSING_OR_UNVALIDATED`
- `STAGE35A_EXISTING_TUNNEL_OAUTH_DISCOVERY_DISABLED`
- `STAGE35A_VERIFY_COMPLETE_NO_REGISTRATION_NO_CUTOVER_NO_SQL_WRITES`
