# Hermes PG Tunnel Stage 31 — fixed one-shot write canary

## Purpose

Validate **ordinary ChatGPT → OpenAI Secure MCP Tunnel → real PostgreSQL → live MT plugin → answer in PostgreSQL** without enabling general-purpose unauthenticated writes. This is a strictly temporary test endpoint, **not production security**.

The previous read-only probe is running as `hermes-pg-readonly-probe.service` and `hermes-pg-tunnel-probe.service`. Stage 31 prepares a new loopback MCP on port **18769**, then stops only the previous tunnel runtime before starting a new runtime on **18770** using the **same existing OpenAI tunnel ID and existing secret file**. This avoids concurrent runtimes sharing one tunnel.

No changes to PostgreSQL schema/permissions, existing OAuth MCP port 8766, Taskboard port 8789, existing MT plugin/config, SSH forward, Cloudflare, or legacy bridge. No new API key, no new domain, no Work mode.

The fixed tool `pg_canary_send_once` has **no arguments**. It invokes a fixed `Catalog().send('chatgpt', task_id, message_id, 'task', body)`. The task ID is the original, already-allowlisted canary `task_f2c64cfdf7894537b35a19a6def977c6`. The message ID is constant, the body is a harmless request for an exact ACK, and PostgreSQL enforces idempotency on that ID. Calling it again cannot create a second distinct message. It checks that the existing 2-message canary ends with an exact MT ACK and that ChatGPT owns the turn before committing. It cannot accept arbitrary SQL, message body, file paths, commands, or other task IDs. `pg_canary_status` reveals only fixed send/ACK booleans, no message bodies.

Even with these limits, **this is not a safe design for a general writable MCP**. Production requires identity-bound access control, separate DB principals/roles, replay/lease semantics audit, backups and recovery tests.

## Responsibilities

- **ChatGPT:** code, tests, risk controls, exact rollout scripts, uses MCP tools from ordinary ChatGPT for verification.
- **User:** save ZIP to Kali Downloads, relay instructions, confirm custom MCP installation in UI (tunnel already exists), supply approvals and credentials only via secure local terminal as necessary; none are needed for this stage.
- **MultiTool agent:** execute exact commands, return markers and safe logs. No coding, manual SQL, Git bridge or alterations to existing plugin configuration.

## Operator commands

Run as `kali` after verifying archive SHA256, and `cd` to the unpacked directory:

```bash
python3 -m py_compile canary_server.py logic31.py test_stage31.py
python3 test_stage31.py
for f in *.sh; do bash -n "$f"; done
bash 31_preflight.sh
bash 31_activate.sh
bash 31_status.sh
```

Expected markers: `CANARY_DB_TURN_AND_AGENT_FILTER_VERIFIED`, `EXISTING_PLUGINS_NOT_MODIFIED`, `STAGE31_READ_ONLY_PREFLIGHT_OK`, `STAGE31_FIXED_ONLY_SERVER_LOCAL_200`, `STAGE31_TUNNEL_WRITE_CANARY_READY_200`, `STAGE31_ONE_FIXED_MESSAGE_NO_ARBITRARY_WRITES`, `STAGE31_READY_FOR_CHATGPT_TEST`.

Next user creates or refreshes a **separate** ChatGPT custom MCP plugin bound to existing tunnel `hermes-pg-v2`, authentication `None` **only for this fixed one-shot test**. The assistant calls `pg_health`, `pg_canary_send_once`, then `pg_canary_status` and checks `roundtrip_complete`.

## Rollback

```bash
bash 31_rollback.sh
```

This stops new runtime/server, removes only the newly created service files, then starts original `hermes-pg-tunnel-probe.service` on port 18768. It preserves PostgreSQL and the old canary task for audit. Does not delete the new program directory.

If activation fails before the switch and creates a partial new directory, **do not retry automatically**; report the error. The installer is designed to stop on unexpected conditions, not to overwrite artifacts.
