# Hermes PG v2 — Stage 34C v2: Managed Inbox Shadow Candidate

This package completes the **managed-only, actor-bound read contract** needed by the general MultiTool inbox, then renders an **unregistered, hard-dry-run** candidate from the exact SHA256-pinned installed ACK-fixed MT plugin. It DOES NOT dispatch or execute any SDK task. It does not update `opencode.json`, any service, Secure Tunnel, current plugin state, or the canary.

## Stage 34C v2: fix to SQL guard (2026-11-22)

**v1 was cleanly refused and rolled back** on `STAGE34C_MANAGED_OR_LEGACY_STATE_CHANGED` because it hard-coded exactly three historical dialogs. The database currently has five including two tombstoned technical test dialogs. **Do not rerun the v1 installer or modify its files.** Unpack this v2 package into a *new* `/home/kali/hermes-pg-stage34c-managed-inbox-v2` directory and run its scripts instead.

The revised SQL no longer checks a fixed count of `bridge.dialogs`; it requires the managed registry be completely empty and confirms the canary still exists in active state at `last_seq=6` with both seq5 and seq6 delivered. Missing canary or receipt rows now fail closed using `NOT EXISTS`, including protection from SQL NULL comparison pitfalls. Historical non-managed rows (active or tombstoned) cannot be admitted automatically. The SQL logs the observed number of dialogs as an informational notice only. All three read endpoints remain managed-only; no dispatch or SDK is activated.

Do not repeat the v2 installer if its outcome is uncertain. Use only `74_sql_only_verify.sh` to reconcile.

## Why an extra SQL migration is necessary

Stage34B's `ag_dispatch_list` filters managed, enabled, agent-turn dialogs, but its response contains no message bodies/IDs. The agent needs a read-only, actor-guarded snapshot to discover exact message IDs and to reconcile an already-started SDK turn after the owner disables dispatch. Without that, it would have to use legacy broad `bridge.view_dialog`, which is unacceptable for the production managed adapter.

Stage34C adds 3 `SECURITY DEFINER` functions, changing schema **4 tables / 39 functions → 4 tables / 42 functions**:

- `ag_view_managed_dialog(text,integer,integer)` — only registered, *previously activated* managed dialogs, active state, 50-message bound; readable after administrative dispatch disable for safe reconciliation.
- `ag_managed_delivery_status(text,bigint)` — only registered, previously activated, ChatGPT-to-agent task receipts.
- `ag_managed_report_status(text,text)` — only agent-authored managed reports, with an absent-result option for send idempotency.

These are granted only to the real `bridge_agent` login. ChatGPT, bridge_app and PUBLIC cannot execute them. No change to the existing 39 functions, messages, outbox or dispatch flags.

## Candidate

- `managed_agent_cli34c.py` reads only `ag_dispatch_list`, `ag_view_managed_dialog`, `ag_read_managed_message`, `ag_managed_delivery_status` and `ag_managed_report_status`. It deliberately **rejects claim, ack, append, create, release** unconditionally, even if a caller passes misleading arguments. SQL is statically parameterized.
- `mt-pg-inbox-managed-stage34c.mjs` is generated from exact installed `mt-pg-inbox-agent-stage33i.mjs` SHA256 `220da977562f9c0763d6b5d5640f686f54f041d357ed397c8482c0d4363f525c`. It has hard-coded `dryRun=true`, DB-managed list rather than the canary allowlist, and a *distinct* state directory `~/.local/state/mt-pg-inbox-managed-stage34c`. **It is not registered** in `opencode.json` and must not be registered yet. The SDK dispatch/prompt code is retained but unreachable in this stage. Legacy fixed-test prompt text will be replaced before a future live managed cutover.
- `ag_dispatch_list` enumerates only enabled agent-turn dialogs. This does **not** by itself ensure reconciliation when an SDK turn ends and `next_actor` changes: the future live managed plugin must first reconcile the durable inflight set independently of this list. This package does NOT claim to close that design gap yet.
- Two actor credentials still reside under the same Kali UNIX user; persistent production requires host-level isolation, future OAuth-authenticated user access, and/or VPS separation.

## Executable order on Kali

Extract under `/home/kali` to `/home/kali/hermes-pg-stage34c-managed-inbox-v2`; invoke as user `kali` with `APPDIR`/`APPIMAGE` removed for Python. The SSH endpoint is supplied privately via `PG_V2_SSH_TARGET` and strict host-key checking.

1. Verify ZIP SHA and bundled SHA256SUMS; run offline tests and syntax checks.
2. `bash 71_readonly_preflight.sh` (must show 39 functions and empty registry).
3. `bash 72_sql_apply_once.sh` **once**. If uncertain, DO NOT rerun. Run `bash 74_sql_only_verify.sh` instead.
4. `bash 73_prepare_unregistered.sh` (only after successful SQL verification; creates one new JS file).
5. `bash 75_verify_only.sh` (read-only DB+config+candidate verification).

No ALTER/DROP of old functions and no dispatch enable occurs. Role separation remains intact. Do not install this candidate, remove `task_ids`, or change the active MCP/Tunnel. Follow-up Stage34D will implement guarded managed claim/ack/report and durable *inflight-before-enumeration* reconciliation, then stage a single managed test with explicit policy enable, separately from the canary.
