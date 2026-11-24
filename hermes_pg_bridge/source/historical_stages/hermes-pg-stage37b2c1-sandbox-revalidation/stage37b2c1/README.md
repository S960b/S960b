# Stage 37B2C1 — revalidate existing candidate only

The Stage 37B2C candidate installation stopped at the original sandbox assertion.
Root cause: the sandbox fake `ctx` omitted `taskIds`. The pinned Stage 34EB
plugin explicitly skips all dispatch work when `ctx.taskIds` is unset, therefore
the original test saw no `stage37b2c_uncertain_claim_hold`. This was a **false
negative caused by the test fixture**, not evidence of a broken intent guard.

This package neither overwrites nor creates a candidate. It confirms the
existing one is byte-for-byte the deterministic rendering of the pinned
original source, then executes its actual `runTick` function through an isolated
Node VM with mock `cliCall`, queue, state, SDK and logger. It includes an
allowlisted-ID positive control and negative controls for missing, empty or
alien allowlists, existing inflight and dry-run.

Installation writes only `/home/kali/hermes-pg-stage37b2c1-revalidate` on success,
a private verifier directory, not runtime state. On failure do not repeat.
No SQL, plugin config, permits, dispatch, actual SDK or service writes.

## Scope and limitations

A passing isolated sandbox shows that the guard behaves as designed under these
mocked conditions; it does **not** establish safe recovery of an actual
PostgreSQL lease after a crash. The existing installed candidate remains
**unregistered and unactivated**. Stage 37B2D deployment should be a separately
reviewed change requiring a maintenance window, restoration path and live
migration analysis.

Run **only** `420_revalidate_candidate_only.sh` (after verifying ZIP hash).
If successful, return all `STAGE37B2C1_*` markers. If STOP, leave candidate
unregistered and send output; do not modify code manually.
