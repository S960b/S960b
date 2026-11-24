# Stage 37B2A — Offline crash-recovery fault injection

## What this tests

The **actual installed, pinned** Stage34EB managed receiver functions `runTick`, `reconcileAnsweredInflight`, and `confirmedMtResponseAfter` are extracted into a strictly isolated Node `vm` sandbox. The code does **not** load/execute the plugin or its imports. All DB responses, state persistence, clocks and ACK/SDK actions are mocked, and the sandbox cannot import network/process/fs modules.

20 independent behavioral scenarios include loss of DB connectivity, loss of ACK reply after commit, wrong report IDs, invalid lease receipts, missed proof, restart with persisted inflight, rate-limit window, dispatch list removal, and non-duplication while an inflight reservation exists.

The Python preflight independently reuses Stage37B1's **read-only** database audit and ensures exact 8-plugin config, completed 37B1 baseline, no pending inflight, no active agent or MCP write permit, inactive dispatch and active Stage37A watchdog. NO PostgreSQL writes, SDK actions, MultiTool restarts, tunnel or plugin config changes.

## Important limits — do not overclaim

**This is not yet a real crash test.** It cannot prove recovery from failure between successful PostgreSQL `claim` and durable `inflight` save. It does not prove SDK session deduplication on restart or automatic recovery from a lost agent report. A failure in those windows requires a dedicated follow-on Stage37B2B protocol/audit before deliberately crashing the real system. It also does not create a new task or test real PostgreSQL lease expiry.

## Execution

Download zip to `/home/kali/Downloads`, verify outer SHA256, extract to a private temporary directory, execute `stage37b2/390_install_and_test_offline.sh`, return all `STAGE37B2_*` markers. If STOP occurs, do not repeat installation; inspect with `391_recheck_readonly.sh` only after install success. Script installs only Python/JS read-only diagnostic files under `/home/kali/hermes-pg-stage37b2-fault-injection`, mode 0600, directory mode 0700.

If `STAGE37B2_FAULT_INJECTION_OFFLINE_PASS_NO_DB_WRITE_NO_SERVICE_CHANGE` appears, the next step is to analyze the **claim-to-state-save gap** and design a separate, bounded live test with disposable dialogue and independent write permits.
