# Stage 37B2 — fault recovery plan

## 37B2A (this package): pure, isolated reproduction

Inspect installed pinned receiver bytes and exercise real recovery functions in an offline Node `vm` with synthetic persisted state, fake database views, ACK and clocks. Inspect finished Stage36 receipts read-only. The target production service never executes simulated code and cannot be modified by these tests.

## 37B2B (separate future package, NOT included)

1. Audit the exact `dispatch` function, lease durations and database `claim` transaction semantics, especially the window **after lease COMMIT but before durable inflight save**. Identify whether there is a recovery token or lease-status recovery method available to the agent. Do not assume `lease` expiry makes SDK re-execution safe.
2. Add an explicit monotonic local persisted dispatch intent or a deterministic recovery state machine, with reviewed failure injection **without** a live SDK invocation. Prove idempotency of the `message_id` and report receipt separately from SDK side effects.
3. On a disposable, new managed test task only, run one bounded live failure test with exact identity/timeout permits, backup/rollback, and user-controlled MultiTool lifecycle. Do not kill/interrupt existing task 36, shared canary, or database/tunnel/MCP.
4. After recovery prove: exactly one report, matching deterministic message ID, no hidden extra SDK session, receipt delivered, dispatch disabled and both gates revoked.

**Do not** treat Stage37B2A offline passing as evidence the real crash window or lease-reclaim behavior is safe.
