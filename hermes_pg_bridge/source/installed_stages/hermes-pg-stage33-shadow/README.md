# Hermes PG v2 — Stage 33A: Shadow Roles

Purpose: install **additive** PostgreSQL capability boundaries without changing the working ChatGPT↔MultiTool bridge. This is NOT production cutover, not credential provisioning and not a fix for the legacy shared role. Two roles, `bridge_chatgpt` and `bridge_agent`, are created with `NOLOGIN` and no password. Nine `SECURITY DEFINER` wrapper functions are created under `bridge_owner`.

- The client cannot supply the sender or dialog initiator: each wrapper hardcodes `chatgpt` or `agent`.
- Every mutating wrapper also compares `session_user` to the exact intended **future login role**. `CURRENT_USER` is not an authentication check in `SECURITY DEFINER` functions.
- No direct `SELECT`/`INSERT`/`UPDATE` rights on underlying tables are granted.
- Existing 16 functions and privileges for legacy `bridge_app` remain usable. Temporary risk remains until stage 33B/34 cutover revokes them.
- No existing data, canary, client configuration, plugin or service is touched.
- Deliberately non-idempotent: if SQL has already been applied, stop and **verify only**. Do not rerun.
- Verification only checks role flags, ACLs, wrapper definitions and harmless aggregate counts. It outputs no message bodies or credentials.

## Manual execution on Kali

Ensure private `PG_V2_SSH_TARGET` is provided through the already authorized SSH environment, with pinned host key. Never print endpoint, password or DSN.

```
sha256sum -c SHA256SUMS
python3 -m unittest -v test_stage33
bash -n 33_install_shadow_roles.sh
bash -n 33_verify_only.sh
bash 33_install_shadow_roles.sh
```

After success, the next phase provisions **separate limited credentials** to the two roles, tests correct/incorrect actor permissions through authenticated sessions, updates Store/MCP/agent worker clients to call only their wrappers, then revokes `bridge_app` function access after draining and backups. This phase performs NONE of those changes.

## Important: delivery status of canary response

`seq=4` currently `pending` is not evidence that the report is lost: the report is persisted and has already been read from DB directly. It means the ChatGPT-addressed outbox record has not been explicitly delivered/acknowledged. A later stage must design and verify safe report-consumption ACK/reconciliation; it must not silently overwrite pending rows.
