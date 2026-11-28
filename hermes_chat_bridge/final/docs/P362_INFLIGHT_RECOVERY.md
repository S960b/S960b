# P36.2: durable inflight reconciliation and offline Taskboard tests

**Release status (2026-11-08): DEPLOYED, SMOKE-VERIFIED, CLOSED.** See [release acceptance record](P362_RELEASE_CLOSED_20261108.md). The staging/deployment gate below describes the historical pre-release condition; NEWS +24h is already completed. This record does not assert that a live SDK failure was injected.

Base: deployed P36.1 commit
\`8e7d3e5019dd51b06a2139793d61f3f03770ef03\`.

## Scope

- Fix an actual P36.1 incident: the MT session can append its response in Git
  while the SDK prompt method fails with an ambiguous error. The local plugin
  retains \`inflight\` to prevent duplicate model execution. A later response
  did not clear that held state, preventing subsequent dispatches.
- On every held active dialogue, poll authenticated/validated Git **read-only**
  at most once every 5 minutes per task. Only if the reserved exact
  \`seq/message_id/from=chatgpt/to=mt\` AND the immediately following MT reply
  are present in the validated Git snapshot, advance \`seen\` to at least the
  reserved seq, remove \`inflight\`, and persist state under the exclusive
  plugin lock. Never resend a possibly already executed prompt.
- The Git read returns at most 100 messages (last-N **ascending**). If the
  reserved message is older and not visible, remain held and require manual
  verification. No time-based clearing or automatic replay.
- A pending later ChatGPT request remains actionable after reconciliation;
  \`seen\` is NOT advanced to the current Git head. Unknown/failed reads retain
  the durable reservation. No new API endpoints or credentials.
- Taskboard HTTP tests inject an offline \`shared_status\` stub into the test
  module so the autouse SQLite isolation guard never opens the live API cache.
  The real Taskboard production path remains unchanged.

## Staging / verification

1. Stage in a clean worktree, never the running detached HEAD.
2. Run \`PYTHONPATH=src python -m pytest -q tests/\` (P36.1 baseline
   was 1173 passed, 2 failures); also compile \`src\`.
3. Run \`node --check deploy/mt-git-inbox-p36.mjs\` and
   \`node --test deploy/p362-inflight-tests.mjs\` and
   \`node deploy/p36-plugin-smoke.mjs\`.
4. Verify missing reply, wrong seq, wrong ID, wrong actor, invalid head,
   network uncertainty, and truncated history all stay blocked. Check
   confirmed MT reply recovers without SDK replay, across process restarts.
5. Report exact results, modified SHA, and any risks in the Git audit task.

**Deployment gate:** NO production application/service/plugin restart or file
replacement while NEWS Phase 2's +24h label is pending. Source branch remains
separate from main. Any eventual merge must preserve NEWS main changes,
and installation requires a separately reviewed, controlled maintenance window
with rollback backup.


## Review correction: dry_run must not reconcile state

The initial P36.2 isolated tests set \`dryRun:true\` but nevertheless
expected the new reconciliation function to clear \`inflight\`. This exposed
a contract violation: reconciliation ran before the existing dispatch-only
dry-run check, so \`seen\` could be advanced even during a dry run.

The corrective patch blocks both confirmed-answer reconciliation and
tombstone reservation cleanup when \`ctx.dryRun\` is true. Regression tests
now exercise real reconciliation in a mocked, isolated \`dryRun:false\`
context with no network/model client and assert dry-run never updates
\`seen\`, \`inflight\`, or the persistent reconciliation throttle.
