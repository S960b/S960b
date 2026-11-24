# Hermes PostgreSQL Bridge v2 — Stage 34E-A (managed runtime shadow)

**Scope: PREPARE ONLY.** This package is authored by ChatGPT and is meant to be
executed by the local `kali` agent under exact commands. No new dialog, no SQL
migration, no PostgreSQL write, no SDK prompt, no role change, no systemd
activation, no update to `opencode.json`, and no Secure Tunnel change.

## Pre-existing state (required)

- Stage34D-A completed. Unregistered candidate
  `/home/kali/hermes-pg-stage34d-write-adapter/mt-pg-inbox-managed-stage34d-shadow.mjs`
  SHA256 `ea06a7fb0fc7c3aa26abb5678de380776e634706a6146fe5b9340a33be956c2d`.
- Active MultiTool 7-plugin config SHA256
  `e5320f6e971073d5d2c43a64ce85d165cb49dc9a94e6cf7806009ebc2b59ded1`.
- `bridge` database: 4 tables, 42 SECURITY DEFINER functions; separate real
  PostgreSQL identities `bridge_chatgpt` / `bridge_agent`.
- No registered managed dialogs and zero enabled dispatch entries.
- Canary `task_f2c64cfdf7894537b35a19a6def977c6` has `last_seq=6`, with
  delivery of seq5 and seq6 already confirmed.
- No managed write-arm file. The active Stage33I canary receiver is untouched.

## Changes made to the UNREGISTERED code

1. Replace the inherited canary-only `buildPrompt()` with a managed-only
   instruction that uses `managed_agent_cli34d.py`, a stable per-task/seq report
   message ID, and requires a private report file. No Git or old canary ACK.
2. Replace the local `cli()` adapter with correct managed-only `claim` and
   `ack` arguments (`task_id`, `seq`, `delivery_id`, `lease_token`, fixed consumer
   `mt-pg-managed`). The Stage34D CLI's separate write gate remains absent.
3. Reconcile ALL durable `state.inflight` reservations BEFORE `ag_dispatch_list`
   is polled. Disappearance from the dispatch list must **never** delete an
   inflight record. A failed or uncertain ACK retains the reservation.
4. Verify the exact deterministic report exists BEFORE ACK even on the normal
   post-SDK path. Just receiving an SDK response is not sufficient.
5. Use a NEW state dir `.local/state/mt-pg-inbox-managed-stage34e` (candidate
   is never registered), so it cannot collide with Stage33I state.
6. Hardcode `const dryRun = true;`. Plugin options cannot enable SDK.

**Stage34E-A DOES NOT turn any of these changes live.** Do not edit `dryRun`
or create the write-arm file: activation requires another reviewed package.

## Usage from `/home/kali/hermes-pg-stage34e-managed-runtime`

```sh
sha256sum -c SHA256SUMS
/home/kali/bridge-pg-v2/.venv/bin/python -m unittest -v test_stage34e
/home/kali/.hermes/node/bin/node --test test_runtime34e.cjs
for f in *.sh; do bash -n "$f"; done
bash 80_preflight.sh
bash 81_prepare_unregistered.sh
bash 82_verify_only.sh
```

The installer is **one-shot**. If it stops after beginning output, DO NOT
re-run. Use `82_verify_only.sh` if a candidate exists, and report the failure.

## Expected confirmations

- `STAGE34E_READ_ONLY_PREFLIGHT_OK`
- `STAGE34E_PREFLIGHT_NO_DB_OR_RUNTIME_MUTATION`
- `STAGE34E_UNREGISTERED_FORCED_DRYRUN_CANDIDATE_CREATED`
- `STAGE34E_MANAGED_PROMPT_AND_IDENTITY_BOUND_ACK_INSTALLED_IN_CANDIDATE`
- `STAGE34E_ALL_INFLIGHT_RECONCILIATION_ADDED`
- `STAGE34E_BYTE_EXACT_PATCH_AND_FORCED_DRYRUN_VERIFIED`
- `STAGE34E_REAL_ACTOR_ROLES_42_FUNCTIONS_EMPTY_MANAGED_REGISTRY_VERIFIED`
- `STAGE34E_CANARY_SEQ5_SEQ6_DELIVERED_UNMODIFIED`
- `STAGE34E_UNREGISTERED_CANDIDATE_VERIFIED_NO_LIVE_CHANGES`
- `STAGE34E_PREPARE_COMPLETE_NO_CUTOVER_NO_SDK_NO_WRITES`

For the next stage, return the generated JS SHA256, test results, and markers.
Do not include passwords, DSNs, lease tokens, or message content.

## Later stages (not authorized by this package)

The managed MCP must have authenticated, user-bound permissions before any
unrestricted writes. A separate stage will create ONE new managed dialog with
explicit `dispatch_enabled` policy, prepare an exclusive process cutover, and
stage a real SDK roundtrip. Only then can an active managed receiver replace
(or coexist under provably disjoint scopes with) the canary receiver.

Lease extension for tasks beyond the current 3600-second lease, and separate
UNIX service identities for the two PostgreSQL secrets, are still outstanding
production requirements.
