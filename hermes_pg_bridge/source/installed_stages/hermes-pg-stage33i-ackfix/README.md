# Hermes PG v2 — Stage 33I-A, ACK reconciliation fix (prepare only)

Stage 33H confirmed: canary `task_f2c64cfdf7894537b35a19a6def977c6` at `seq=6` and **both seq5 and seq6 delivered**. The active role-bound inbox plugin (`SHA256 3535ae7b...`) has a loss-of-receipt bug: its `reconcileAnsweredInflight()` sees the report after an SDK timeout and clears local `inflight` without PostgreSQL `ack_outbox()`. This stage prepares a fixed candidate; **does not activate it**.

## Exact source guards

- Original plugin SHA256: `3535ae7bd5bb0da3e8574afcab299118dac744bc851c4bcb90b8656acd8b3ea7`.
- Original actor CLI SHA256: `13f098ecd5367345880c9616feaa2d36ca3f6a9d1f4b93ec03dd7529200ed4cc`.
- Current `opencode.json` SHA256: `a8f11a25173b5e7f1290d97b4aedfaea24403256fa09c16144093e0f9d297cb3`.
- Exact canary `task_id`, message IDs, fixed report body, role-specific client, seven-plugin config and `dry_run=false` must be unchanged.
- Two new candidates are created with mode 600 in `/home/kali/hermes-pg-stage33i-ackfix`. They are **not** registered in `opencode.json`.

## Fix implemented

1. In the plugin's `reconcileAnsweredInflight`, after an independently verified report, require a retained lease `delivery_id` / `lease_token`, call actor-bound PostgreSQL ACK and verify a positive result **before** advancing `seen` and deleting `inflight`. When ACK outcome is unknown, keep `inflight` and never prompt SDK again.
2. In the agent actor CLI, `ack` remains fixed to **seq5 only**. A `delivered` receipt with the same `delivery_id` is treated as idempotent success; `pending`, a mismatching `delivery_id`, bad token or wrong consumer fail closed. A lost ACK response triggers a read-after-uncertain-write. No claim/release/reset is performed.
3. The new plugin retains the SAME durable state directory as the live Stage 33F plugin. It must be switched **only when all old MultiTool processes have completely exited**. No switch occurs here.

## Execution, under Kali user

Archive first in `/home/kali/Downloads/hermes-pg-stage33i-ackfix.zip`, verify archive SHA provided by ChatGPT, extract into `/home/kali`, verify `SHA256SUMS`. From `/home/kali/hermes-pg-stage33i-ackfix`:

```bash
python3 -m unittest -v test_stage33i test_stage33i_cli
bash -n 54_prepare_unregistered_ack_fix.sh
bash -n 55_verify_only.sh
bash 54_prepare_unregistered_ack_fix.sh
bash 55_verify_only.sh
```

The prepare script calls real PostgreSQL **read-only** preflight under both roles over existing 15433 and validates `seq5=delivered, seq6=delivered`. It renders 600-permission files and runs Node's real implementation tests using `new Function` over **only the extracted reconciliation function**, fake DB results, no SDK import, no actual plugin activation.

## Boundaries

NO SQL writes, NO SDK prompt, NO claim, NO ack against the real DB, NO `opencode.json` changes, NO service changes, NO restart, NO Git bridge, NO new canary message. **Do not run `54_prepare_unregistered_ack_fix.sh` twice.** `55_verify_only.sh` is read-only and can be repeated.

The prepared fix addresses the observed 11-minute run while the 3600-second lease is valid. It does **not** magically extend a timed-out lease or resolve long runs beyond the lease duration; production needs a lease-renewal or verified post-completion receipt protocol. This is a fixed-canary plugin, not yet a general-purpose task dispatcher.

## Expected markers

- `STAGE33I_STRICT_PRECHECK_BOTH_RECEIPTS_DELIVERED`
- `STAGE33I_REAL_ACTOR_DB_LOGINS_AVAILABLE`
- `STAGE33I_CURRENT_PLUGIN_AND_CONFIG_HASH_PINNED`
- `STAGE33I_CANDIDATES_CREATED_UNREGISTERED`
- `STAGE33I_ACTIVE_PLUGIN_AND_CONFIG_UNCHANGED`
- `STAGE33I_NO_SDK_NO_ACK_NO_DB_MUTATION`
- `STAGE33I_NODE_RECONCILE_6_TESTS_OK`
- `STAGE33I_BOTH_ACTOR_ROLES_AND_SEQ5_SEQ6_DELIVERED`
- `STAGE33I_ACK_RECOVERY_CODE_MATCHES_PINNED_SOURCE`
- `STAGE33I_CANDIDATES_NOT_REGISTERED`
- `STAGE33I_NO_RUNTIME_OR_DATABASE_CHANGE`
- `STAGE33I_VERIFY_ONLY_COMPLETE`
- `STAGE33I_A_PREPARE_COMPLETE_NO_CUTOVER`
- `STAGE33I_A_VERIFY_ONLY_SUCCESS`

Report both new candidate SHA256 values and markers. No secrets, no DB messages, no runtimes touched.
