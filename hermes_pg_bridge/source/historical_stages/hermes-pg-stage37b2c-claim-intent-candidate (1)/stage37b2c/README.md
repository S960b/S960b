# Stage 37B2C — Durable pre-claim intent, **unregistered candidate**

## Proven failure and design

Stage 37B2B identified `claim` before local `inflight` persistence in the installed
Stage 34EB live-capable managed handler, source SHA `b3679086583d…`. SQL claim and
local state write are separate transactions. After a crash between the two, the
process may have an outstanding DB lease without a locally usable token. It is
**not safe to assume lease expiration makes an SDK replay permissible**.

37B2C generates a byte-anchored replacement **without installing it into
MultiTool**. The replacement imports a small, separated intent journal helper:

1. Before calling the pinned `claim` function, create a private, exclusive,
   task-ID-keyed intent file (one per dialog) and `fsync` the file **and directory**.
2. On restart, if that intent exists but no confirmed ACK cleanup is recorded,
   `runTick` blocks redispatch for that dialog. No second claim or SDK execution.
3. Existing `inflight` reconciliation is preserved and still runs **before**
   scanning pending work. Only a successful existing ACK path + updated local
   `seen` and cleared `inflight` may remove the intent file.
4. If the claim reply is lost and no lease token is durably held, the intent
   remains **operator-intervention-required**; there is deliberately no automatic
   lease-token reconstruction or blind re-claim.

The candidate keeps using the existing Stage 34E state directory and Stage 34D
CLI, and it retains the exact allowed managed `task_id`. The existing 8-plugin
`opencode.json` is not modified.

## Instructions

1. Keep both write gates absent and dispatch disabled. Leave MultiTool running.
2. Install from this exact ZIP with `410_prepare_candidate_only.sh`. The script
   resolves Node using `/home/kali/.hermes/node/bin/node`, avoiding the PATH
   issue in 37B2B. It verifies SHA256SUMS and all offline tests first.
3. Source preflight uses existing Stage 37B2A read-only PostgreSQL/process
   baseline and SHA-pins the installed live plugin. Any unexpected source
   structure aborts instead of guessing. The package only creates files under
   `/home/kali/hermes-pg-stage37b2c-claim-intent`.
4. Return the `STAGE37B2C_*` markers. The candidate is deliberately not
   registered, and the live 8-plugin system does not reload.
5. `411_verify_readonly.sh` is a safe subsequent check.

## Testing / limits

- Python source-contract unit tests use a synthetic known-shape source.
- Node tests exercise journal durability API, identity checks and strict
  idempotent behavior, with temporary filesystem directories only.
- An isolated VM executes **the actual rendered candidate's** runTick with a
  mocked DB/SDK to verify that a pending intent blocks redispatch.
- These tests do **not** establish production crash consistency for the complete
  SQL/SDK stack. No real lease is taken, no database write, no service restart,
  no plugin configuration change, and no write permit is granted by this stage.
- Activation or replacement of the live plugin is deliberately postponed to
  a separate Stage 37B2D, after reviewing on-host candidate markers and source
  shape. Keep this a separate safety gate.
- The private journal records task ID, seq and message ID, **not** lease tokens,
  secrets, prompts or report bodies. An unresolved journal is a hold, not a
  license to retry; do not delete journal files manually during a real incident.

NOTE: Normal success cleanup is inserted only after the pinned successful ACK
path. A crash after ACK but before cleanup can leave a conservative held intent;
this is safe against duplicate SDK execution but may require manual recovery.
