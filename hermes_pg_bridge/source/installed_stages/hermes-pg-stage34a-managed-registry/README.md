# Hermes PG v2 — Stage 34A: shadow managed-dialog registry

## Why this stage is necessary

**NEVER remove `task_ids` from the current MT plugin before implementing a dispatch admission gate.** The old `bridge.list_dialogs()` returns all active legacy dialogues, including work that has never been authorized for SDK dispatch. A cross-process file lock prevents simultaneous execution, but does **not** authorize which work may run.

Stage 34A adds an explicit PostgreSQL registry `bridge.managed_dialogs` and five role-bound SECURITY DEFINER functions. Existing tasks, reports, receipts, MT plugins, `opencode.json`, Secure Tunnel and services remain unchanged. **No old dialogues are automatically registered.** Every new managed dialogue is created with `dispatch_enabled=false`. The agent-discovery API returns **only** active, enabled managed tasks waiting for the agent. Registration does not equal authorization to execute.

## New API

- `bridge.cg_create_managed_dialog(request_id,title)` — creates a real dialogue and enrolls it **disabled**, idempotent request ID, rejects a pre-existing unmanaged request ID
- `bridge.cg_managed_status(task_id)` — ChatGPT reads enrollment and approval state
- `bridge.ag_dispatch_list(offset,limit)` — agent sees **only** enabled managed dialogs requiring its turn; empty immediately after migration
- `bridge.ag_claim_managed_message(task_id,seq,message_id,consumer,lease_seconds)` — refuses legacy or disabled tasks, and takes a share lock on admission while claiming
- `bridge.ag_send_managed_report(task_id,message_id,body,expected_version)` — agent can report in a managed dialogue after executing a previously allowed task; disable must not strand in-flight work

All five are SECURITY DEFINER with strict `SESSION_USER` guards, with granular role grants and no table access. The old role-bound functions remain for backward compatibility in this *shadow* stage. Before production we will change the agent CLI to use only managed API and then revoke unnecessary broad agent permissions. This stage **does not** implement OAuth for general MCP writes, automatic activation, leased-message renewals, or ChatGPT report ACK; those require their own validated stages.

## Preconditions

- PostgreSQL has **3 tables / 25 functions**, roles `bridge_chatgpt` and `bridge_agent`, both real logins, existing `bridge_owner` NOLOGIN
- Current canary is active with `last_seq=6`, deliveries 5 and 6 `delivered`
- Installed plugin and config pinned to Stage 33I-B: plugin SHA `220da977…`, config SHA `e5320f6e…`
- Persistent actor SSH 15433 available; server SSH through the private `PG_V2_SSH_TARGET` already known to the operator
- Stage 33J completed; no MultiTool SDK runs in this stage

## Execution sequence

1. Check archive SHA256, extract into `/home/kali/hermes-pg-stage34a-managed-registry`, verify `SHA256SUMS`; run Python offline tests and `bash -n` on each shell script.
2. Run `bash 64_readonly_preflight.sh`.
3. Run `bash 65_install_once.sh` **exactly once**. It applies transactional SQL as PostgreSQL administrator over the existing pinned SSH channel, then verifies the two real roles. It executes a new-dialog create+retry **inside a rolled-back transaction**; no test message or dialogue is committed.
4. Run `bash 66_verify_only.sh`. This verifies role grants, canary, legacy exclusion, empty managed dispatch, and unchanged plugin config.

If SSH disconnects or SQL fails during DDL, **never automatically rerun step 3**. Use **only** step 4 and return logs. PostgreSQL `BEGIN/COMMIT` means a process/connection failure may have an ambiguous client outcome.

## Explicit non-goals

- No removal/relaxation of the current canary `task_ids`
- No MT SDK invocation, claim, ACK, report or queue processing
- No Secure Tunnel switch, MCP registration or new public endpoint
- No existing dialogue activation; no `dispatch_enabled=true` changes
- No secrets, table data, message bodies or full environment variables in logs
- The emergency rollback SQL is supplied for review **only**. Never run it unless the owner explicitly authorizes backout and the registry is still empty.

## Next stage

Stage 34B will implement a two-role, authenticated/authorized general MCP and an MT candidate consuming ONLY `ag_dispatch_list` and `ag_claim_managed_message`. First authorize one newly created managed dialogue and verify end-to-end; only then consider enabling multiple tasks. Because the current tunnel's MCP has no user-level OAuth, it must NOT be replaced by an unrestricted writable MCP at this stage. See https://developers.openai.com/plugins/build/auth and https://developers.openai.com/api/docs/guides/secure-mcp-tunnels.
