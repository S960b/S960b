# Stage 33H: exact ChatGPT receipt + pinned plugin source inventory

Stage 33G already closed the `seq=5` delivery. Stage 33F-SDK roundtrip was independently verified through Secure MCP Tunnel with `seq=6`, exact body, seq5 delivered. This small stage addresses only the still-`pending` ChatGPT-addressed receipt for seq6. There is no task send, no SDK invocation and no plugin or schema change.

**One-off recovery, not a production receipt implementation.** To record the already-confirmed ChatGPT consumption of the exact `seq=6`, a single guarded administrative PostgreSQL transaction validates the immutable task/report, delivered seq5 and pending seq6. It creates a canonical lease and calls the existing `bridge.ack_outbox()` inside the same transaction without exposing the lease token. Any drift aborts and rolls back. The temporary admin path will later be replaced with actor-bound `bridge_chatgpt` recipient claim/ACK APIs in the production MCP.

The separate `source_inventory33h.py` reads ONLY the currently registered plugin source (fixed SHA256), an exact config hash, and limited static code excerpts. It doesn't read state, passwords, logs, environments or call SDK. The returned snippets let ChatGPT write the *next* plugin patch against actual deployed code instead of guessing. This is deliberately not an automatic plugin edit: safe changes require a coordinated restart with verified exclusive ownership.

## Order

1. Verify package SHA, SHA256SUMS, unit tests and `bash -n`.
2. Read-only preflight: `bash 50_preflight_readonly.sh` (must show `seq6_pending`).
3. Single guarded update: `bash 51_accept_seq6_once.sh`. DO NOT repeat on error; call only `bash 52_verify_only.sh` to reconcile uncertain outcome.
4. Static source inventory: `bash 53_inventory_plugin.sh`. Return the output (no secrets). Even if receipt step fails, read-only inventory is allowed.

Use Kali user, `env -u APPDIR -u APPIMAGE` when calling Python from AppImage context, private `PG_V2_SSH_TARGET` pointing to already-known DB server. No Git-mост. Never print credentials, private SSH address or DSN.

Expected: `STAGE33H_SEQ6_PENDING_EXACT_REPORT_PRECHECK_OK`, `STAGE33H_READ_ONLY_PREFLIGHT_OK`, `STAGE33H_EXACT_SEQ6_RECEIPT_COMMITTED`, `STAGE33H_SEQ6_ONE_RECEIPT_TRANSACTION_COMMITTED`, `STAGE33H_SEQ6_DELIVERED_AND_SEQ5_UNCHANGED`, `STAGE33H_READ_ONLY_POSTCHECK_COMPLETE`, `STAGE33H_PINNED_PLUGIN_SOURCE_INVENTORY_OK`.

## Explicit non-goals

Does not fix the plugin's ACK-on-reconciliation bug, does not authorize other tasks, does not modify any users, grants, schema or systemd, does not remove `armed-stage33fc`, does not change `inflight` / `seen` or current plugin. The next patch will preserve the lease credentials persistently and only clear `inflight` after a positive ACK, with an idempotent recovery path for ambiguous ACK outcomes and no SDK re-execution.
