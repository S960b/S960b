# Stage 37B2B — offline analysis of claim-before-inflight crash window

## Goal

Establish the precise order of `claim`, persisted `inflight`, and external SDK invocation by inspecting the installed exact pinned Stage34EB plugin source. No real SDK, PostgreSQL mutation, state file editing, crash, service restart, plugin registration, or permission is involved.

The offline finite-state model demonstrates an **illustrative** failure mode and a fail-closed pre-claim intent journal design. It is not proof that the live production implementation has been repaired.

## Source reasoning

Stage34A `ag_claim_managed_message` checks registration and dispatch, then calls `ag_claim_message`. Stage34D managed CLI issues claim in a separate database transaction from the Stage34E plugin's local state persistence. If `claim` finishes before the local durable record is committed, a crash can strand a leased task without a recoverable lease token. The SQL receipt and SDK side effects require separate proofs. A deterministic report ID does not make SDK calls idempotent.

The executable auditor distinguishes source observations from conclusions. In particular it does **not** assert lease expiry automatically releases a task: that needs exact SQL / reaper examination.

## Steps

1. Keep all permissions revoked; no live managed job may be pending.
2. Run `400_install_and_audit_readonly.sh` once from the extracted archive as `kali`.
3. Return only `STAGE37B2B_*` markers, not plugin content, body, tokens or raw logs.
4. Do not patch/restart anything. The resulting source ordering will determine Stage37B2C patch proposal.

## Security

- Absolute paths and SHA256 pin for the installed plugin.
- Refuses active ChatGPT or local agent write capabilities.
- Refuses unexpected 8-plugin configuration.
- Assumes Stage37B2A has already reverified the database/delivery baseline read-only.
- Does not copy or output secrets or full plugin source.
- Installation creates only its own private directory.
- Illustrative model is run in a separate process without dynamic access to the plugin.

## Likely remediation (not applied)

Before calling `claim`, write and synchronize a deterministic `PRE_CLAIM_INTENT` record keyed by task ID and message ID. If process restarts and sees the intent without a recovered verified lease token, block re-claim and SDK replay, and require an explicitly audited reconciliation mechanism. Follow up with DB-side observation/release semantics and bounded tests. Do NOT erase an ambiguous intent merely because a lease TTL has passed.
