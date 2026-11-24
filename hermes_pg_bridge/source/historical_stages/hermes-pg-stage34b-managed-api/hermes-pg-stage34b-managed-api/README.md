# Hermes PG v2 — Stage 34B: Managed Dialogue API (shadow-only)

This is a **one-shot additive SQL migration** followed by an isolated, read-only MCP preview. It does NOT expose write tools, connect to the OpenAI Secure MCP Tunnel, activate managed dispatch, register a MultiTool plugin, or send a durable message.

## Preconditions

- Stage 34A installed: 4 tables, 30 functions, `managed_dialogs` empty.
- Running MultiTool config still has **7 plugins** with `mt-pg-inbox-agent-stage33i.mjs` under exact canary-only filter. Existing SHA checks are delegated to the pinned Stage 34A `registry34a.pin_runtime` implementation.
- Both PostgreSQL roles log in via the systemd SSH forward at 127.0.0.1:15433.
- Canary last_seq=6 and deliveries seq 5/6 delivered.
- A live, owner-authorized SSH endpoint privately in `PG_V2_SSH_TARGET`; do not print endpoint or secrets.

## New features (SQL, 9 SECURITY DEFINER functions)

`bridge.cg_list_managed_dialogs`, `cg_managed_detail`, `cg_set_managed_dispatch`, `cg_send_managed_task`, `cg_view_managed_dialog`, `cg_claim_managed_report`, `cg_ack_managed_report`, `ag_read_managed_message`, `ag_ack_managed_message`.

`managed_dialogs` gains `policy_version bigint NOT NULL DEFAULT 0`. A newly created managed dialogue is still disabled by default and can only be enabled by an explicit `bridge_chatgpt` caller with both expected policy and dialog versions. No old task can be registered by this migration. Agent discovery remains `ag_dispatch_list` and returns only dispatch-enabled managed dialogs; it is empty in this stage.

The existing broad role-bound wrappers from Stage33A remain available **temporarily**. Full role hardening and removal of those older broad grants must occur only after the replacement clients have been verified. Never expose a general-write MCP via a no-OAuth tunnel.

### Important limitations

- The `cg_set_managed_dispatch` SQL operation provides a server-side control, **not proof of user authorization**. Future exposed MCP requires OAuth and explicit user authorization for writes.
- Agent claim remains `ag_claim_managed_message` from Stage 34A. Adapter code must never call generic `ag_claim_message` for managed dispatch after cutover.
- This stage intentionally keeps the fixed canary plugin in production; it does not process other dialogues.
- The verification's create/enable/send/disable sequence occurs only in a transaction that is explicitly `ROLLBACK`-ed; there is no committed dialogue or queue entry.
- A remote connection can fail after successful migration. Do not rerun one-shot SQL; use the read-only verification script.

## Run order under user `kali`

1. Extract ZIP to `/home/kali` and `cd /home/kali/hermes-pg-stage34b-managed-api`.
2. Check package SHA256 and internal `SHA256SUMS`; run `python3 -m unittest -v test_stage34b` and `bash -n *.sh`.
3. `bash 67_readonly_preflight.sh`.
4. `bash 68_apply_sql_once.sh` **exactly once**.
5. `bash 69_verify_only.sh`.
6. Optional but planned: `bash 70_install_readonly_shadow.sh`. It starts `hermes-pg-stage34b-shadow.service` on 127.0.0.1:18775, static/no autostart, with one read-only tool `stage34b_managed_api_health`.

Do **not** add this MCP to the OpenAI Tunnel, even though it uses a real PostgreSQL role: it has no user authentication. It is only an internal readiness probe.

After any ambiguous SQL result, run `bash 69_verify_only.sh`, not the install again. This package intentionally contains no automatic DDL rollback.
