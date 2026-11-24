# Hermes PG v2 — Stage 34D-A: managed write adapter and SDK source audit

**Scope: offline/shadow preparation only. No SQL migrations, no database writes,
no new tasks, no dispatch enabling, no plugin registration, no SDK invocation.**

Stage 34C v2 (4 tables, 42 functions, 0 managed registry entries) must be complete.
This package is a follow-on to that installed build and is designed for user `kali`.

## Why this phase is necessary

`bridge.ag_dispatch_list()` lists managed dialogs *only when the agent is the next
actor*. Once the agent commits its report, the dialog disappears from that list.
A naïve copy of the older MT plugin would therefore miss outstanding `inflight`
records and might never ACK them. Before enabling real managed SDK execution,
Stage 34E must independently reconcile **all local durable `inflight` records**,
including dialogs no longer in the dispatch list, and must keep reservations
until a matching PostgreSQL ACK is confirmed. It must also replace the old
fixed-canary `buildPrompt()` with a managed-dialog prompt.

## Files

- `managed_read34c.py`: pinned read-only Stage34C CLI copy, not broad bridge APIs.
- `managed_agent_cli34d.py`: actor-bound CLI candidate adding managed-only
  `claim`, `ack` and `append/report`, with a separate hard write gate absent on Kali.
  The CLI cannot write while that gate is absent. Do NOT create its gate file.
- `render34d.py`: constructs an **unregistered, forced-dry-run** JS candidate
  from the byte-pinned Stage34C candidate and extracts current JS source excerpts.
  **Do not turn dry-run off:** the SDK prompt and inflight enumeration still
  need Stage34E review before any activation.
- `probe34d.py`: read-only PostgreSQL roles/schema/canary check and byte pins.
- `76_preflight.sh`, `77_prepare_unregistered.sh`, `78_verify_only.sh`,
  `79_source_summary.sh` and offline tests.

## Execution

Unzip as `/home/kali/hermes-pg-stage34d-write-adapter` and execute from that
directory, after checking the outer SHA256. Then:

```
sha256sum -c SHA256SUMS
/home/kali/bridge-pg-v2/.venv/bin/python -m unittest -v test_stage34d
for f in *.sh; do bash -n "$f"; done
bash 76_preflight.sh
bash 77_prepare_unregistered.sh
bash 78_verify_only.sh
bash 79_source_summary.sh
```

`77_prepare_unregistered.sh` is **one-shot**. Never repeat after a partial
failure; inspect with `78_verify_only.sh` and report. No database transaction
or runtime change occurs in this stage.

Expected:

- `STAGE34D_READ_ONLY_PREFLIGHT_OK`
- `STAGE34D_TWO_ACTOR_ROLES_42_FUNCTIONS_AND_EMPTY_REGISTRY_VERIFIED`
- `STAGE34D_UNMANAGED_CANARY_SEQ6_BOTH_DELIVERED`
- `STAGE34D_UNREGISTERED_DRYRUN_CANDIDATE_CREATED`
- `STAGE34D_WRITE_GATE_DENIED_WITHOUT_SQL_MUTATION`
- `STAGE34D_INERT_CANDIDATE_PINNED_SOURCE_CONTRACT_COLLECTED`
- `STAGE34D_NO_ACTIVE_MCP_OR_MT_CONFIG_CHANGE`
- `STAGE34D_PREPARE_COMPLETE_NO_CUTOVER`
- `STAGE34D_VERIFY_ONLY_NO_MCP_OR_SDK_SWITCH`
- `STAGE34D_PINNED_PLUGIN_SOURCE_EXCERPTS_COMPLETE`

For Stage34E, return the sanitized source excerpts of `buildPrompt`,
`confirmedMtResponseAfter`, `reconcileAnsweredInflight`, `runTick` and `dispatch`
from `79_source_summary.sh` (these are program source, not runtime messages).
No secrets or chat content are read from the database by the source extractor.

**Never** register the Stage34D candidate in `opencode.json` or remove the
canary filter at this stage. Do not run the agent CLI with a mutation command
outside the contained write-denial test.
