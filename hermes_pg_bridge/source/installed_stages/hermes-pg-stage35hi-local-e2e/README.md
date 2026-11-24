# Stage 35H + 35I (one execution, no cutover)

ChatGPT authored; the agent only executes the provided shell script and returns **marker lines**, not raw private logs.

## Why this test is stronger than `tunnel-client run --help`

The **installed** OpenAI `tunnel-client` binary runs in its officially documented **local-only** `dev proxy` mode with a local in-memory control plane (no OpenAI control plane, no credentials, no production tunnel ID). The client gets:

```
MCP_EXTRA_HEADERS=X-Bridge-Local-Key: file:/home/kali/.config/bridge-pg-v2/managed-mcp-local-key35c
MCP_SERVER_URL=http://127.0.0.1:18777/mcp
MCP_OAUTH_DISCOVERY=disabled
```

The external MCP test client deliberately **does not know or send the local secret**. A successful `initialize`, `tools/list`, `managed_health` and `managed_list` via the dev-proxy ingress therefore demonstrates actual `file:` resolution and request forwarding by the installed tunnel-client, not just syntax support. Strict read-only API calls only, and real role-bound PostgreSQL verification. Includes 403 checks established by 35G.

If `dev proxy` is absent in installed v0.0.21, or if it does not honor runtime `MCP_EXTRA_HEADERS`, stage stops **without production cutover**. Do not retry without an updated package.

## Outputs and safety

- Starts and stops only the already-installed, patched `hermes-pg-stage35c-static-shadow.service` (port 18777); never starts the old 35D/35E/35F/35G smoke scripts.
- Starts and stops only a foreground child `tunnel-client dev proxy` with local TCP ingress and local control plane.
- A private 0600 dev-proxy log remains under the new 35HI directory, **not** to be pasted into chat.
- Existing production tunnel `hermes-pg-tunnel-stage33fd.service` on 18773, seven plugins, PG write gate and database unchanged.
- One-shot marker is written **before** starting the service; never repeat after an ambiguous failure.
- Prepares `run_stage35hi_candidate_NOT_ACTIVATED.sh` as **inactive, deliberately unarmed** candidate for a later independently reviewed, rollback-capable cutover stage. It uses the original runtime key and tunnel ID, a file-backed MCP header, destination 18777, and health port 18778. This step does **not** start it.

## Success

`STAGE35H_REAL_CLIENT_FILE_BACKED_HEADER_INITIALIZE_OK_SSE` (or JSON),
`STAGE35H_REAL_CLIENT_TUNNEL_11_TOOLS_LISTED`,
`STAGE35H_REAL_CLIENT_READ_ONLY_PG_ROLE_AND_PILOT_OK`,
`STAGE35H_FILE_BACKED_HEADER_PROVEN_REAL_CLIENT_READ_ONLY`,
`STAGE35I_CANDIDATE_LAUNCHER_PREPARED_NOT_ACTIVATED`,
`STAGE35HI_COMPLETE_OLD_TUNNEL_ACTIVE_NO_PLUGIN_CHANGE_NO_DB_WRITES`.

### Sources for transport contract

- https://github.com/openai/tunnel-client/blob/master/docs/development.md (`dev proxy` integration testing)
- https://github.com/openai/tunnel-client/blob/master/docs/configuration.md (`MCP_EXTRA_HEADERS` `file:` header reference)

These docs describe upstream behavior; actual support is **only confirmed by the live local 35H test**, not by copying the docs.
