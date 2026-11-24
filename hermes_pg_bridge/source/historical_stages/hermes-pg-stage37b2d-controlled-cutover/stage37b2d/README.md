# Hermes PG — Stage 37B2D: controlled cutover of managed Inbox to durable-intent candidate

## Known state (from Stage 37B2C1)

- The current 8-plugin MultiTool config registers the Stage34EB live-capable managed Inbox with `dry_run=false`, `tick_ms=30000` and exactly `task_b6c26aa18220412bafdbf32393f16568` in `task_ids`.
- Stage37B2C1 verified the UNREGISTERED Stage37B2C candidate and the corrected 7-scenario sandbox. This did **not** prove real PostgreSQL crash recovery.
- Stage36 E2E finished: seq1 task and seq2 report delivered once, `dispatch_enabled=false`; Stage37A watchdog active, MCP permit and agent CLI gate absent.
- This stage changes ONLY the eighth plugin's registered source path after all MultiTool processes are stopped. No new task, SQL write, service control, SDK invocation or permit issuance is allowed.

## What changes

Only the path in the managed plugin entry of `/home/kali/.config/gigatool/opencode.json`:

OLD: `/home/kali/hermes-pg-stage34eb-pilot-candidate/mt-pg-inbox-managed-stage34eb-pilot-unregistered.mjs`

NEW: `/home/kali/hermes-pg-stage37b2c-claim-intent/mt-pg-inbox-managed-stage37b2c-unregistered.mjs`

No other bytes in the config change. The candidate loads an absolute-path helper `intent37b2c.mjs` and adds a private, fsynced pre-claim journal. For a claim whose outcome is unknown, it **holds the dialogue**, not blindly retries the SDK. Existing `inflight` reconciliation is preserved. Full automatic recovery of an uncertain lease is **not** implemented.

## Safe steps

**1. Prepare while MultiTool is running.** Extract this ZIP, run `430_install_prepare_only.sh`. It verifies this bundle's checksums, 43 offline Python tests, Node's syntax and real candidate sandbox, exact source/renderer/helper checksums, current pinned config (the complete SHA is obtained from Stage36B's private manifest, and must start `b623e140760c`), local state and read-only database receipts. It creates the new private directory `/home/kali/hermes-pg-stage37b2d-safe-cutover`, a byte-exact config backup, a one-path-change draft, and a manifest. Active config remains unchanged.

**2. Stop all MultiTool instances** in the GUI. Keep MCP, tunnel, PostgreSQL, and Stage37A watchdog running. From a **normal Kali terminal outside MultiTool**, call `431_commit_when_multitool_closed.sh` once. It refuses to proceed while MultiTool or unknown processes exist, if the agent's Inbox lock remains, if either write capability appears, if dispatch changes, if the DB receipts change, if the new candidate differs by a single byte from the pinned renderer or source, or if current config does not match the pinned old SHA. It writes a one-shot journal *before* atomically replacing the config. It never kills processes.

**3. Reopen ONE MultiTool instance.** Run `432_verify_after_restart.sh`, a read-only check. It asserts new config SHA, exactly one managed candidate path, unchanged read-only DB receipts, empty `inflight`, no claim intents, no write capabilities and active watchdog. It cannot prove the new plugin was dynamically imported and its handler invoked just from process counts; that requires a later instrumented runtime probe. Do NOT enable dispatch or write permissions based only on this test.

**Status**: `434_status_readonly.sh` after successful preparation, including after a STOP. It does not query PostgreSQL or change config.

**Rollback**: close all MultiTool processes. Run `433_rollback_when_multitool_closed.sh` once only after reviewing state. It verifies original DB state and no journal or `inflight`; restores backup config byte-for-byte if config is exactly the expected new SHA. It refuses an unknown SHA. If commit was partially completed, do NOT rerun it. Use status then rollback. No read/write permits are needed. Reopen ONE MultiTool if rollback succeeds.

**Emergency deny** remains Stage37A: `bash /home/kali/hermes-pg-stage37a-permission-control/371_emergency_revoke.sh`; separate from this config cutover. This stage never enables writes, thus normal work remains idle.

## Critical limitations

- A task already claimed by the old handler cannot be seamlessly migrated by this package; preflight insists the known test dialogue is closed and no local `inflight` or claim-intent journal exists.
- The candidate is a **conservative safety improvement**, not proof of production crash-consistency and not a permanent lease-recovery design.
- Uncertain claims become an operator-visible hold; never delete the intent file to force a replay. A database-side, independently verifiable lease resolution procedure is required for safe recovery.
- User should not manually change `opencode.json`, restart background services, issue permissions, or send test messages during this rollout.
- Files are private (`0700` install directory, `0600` backups), with durable file and directory fsync for config replacement and one-shot markers.
