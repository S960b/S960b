# Hermes PG v2 — Stage 33J: real PostgreSQL ACK reconciliation, with simulated lost reply

## Status before the test

Stage 33I-B: installed `mt-pg-inbox-agent-stage33i.mjs`, exactly seven plugins, Git removed, active canary allowlist. Production canary `task_f2c64cfdf7894537b35a19a6def977c6` has `last_seq=6` and both `seq5/seq6=delivered`; live plugin's durable `seen=5`, `inflight={}`. The same system user can currently access both PG actor credentials; this is a test environment only.

## Test design

This test **does not call the MultiTool SDK** and does not submit a new canary message. It creates a separate fixed, harmless technical dialog with one `chatgpt:task` (`seq1`) and an `agent:report` (`seq2`), using real `bridge_chatgpt` and `bridge_agent` logins and actual role-bound SQL wrappers. The existing MT plugin is hard-filtered to the original canary task, so it does not process the isolated dialog.

The test claims this new task once, but intentionally does not ACK before the report. It then extracts the exact `confirmedMtResponseAfter()` and `reconcileAnsweredInflight()` functions from the **byte-pinned currently installed plugin**, evaluating these two functions in a standalone Node process with an isolated temporary durable state. It never imports/registers the plugin or MultiTool SDK and never touches its production `state.json`.

The first `reconcile` call performs a **real** PostgreSQL `ag_ack_outbox()` under `bridge_agent`, then deliberately throws a simulated transport exception *after the DB commit* (lost ACK response). It must keep the isolated `inflight`. The second call confirms the already-delivered receipt by `delivery_id`, updates the isolated `seen`, and clears that isolated `inflight` **without calling SDK**. Finally the test tombstones only its technical dialog and cancels its remaining chatgpt-addressed report outbox. This creates no live work for MultiTool and cannot dispatch arbitrary task IDs.

**Only one dialog is permitted** (fixed `request_id` and fixed message IDs). If anything fails after creation, DO NOT re-run `62_run_once.sh`; use read-only `63_verify_only.sh` and send diagnostics. No automatic recovery/cancellation of uncertain transactions.

## Files

- `stage33j.py`: strict one-shot DB stage, read-only checks, child helper for agent-bound ACK, and verification
- `reconcile33j.cjs`: independently executes the ACTUAL installed reconciliation functions in a sealed test context, with real DB ACK and simulated lost reply
- `test_stage33j.py`: offline safety/unit checks
- `61_preflight.sh`, `62_run_once.sh`, `63_verify_only.sh`

## Steps

Under Kali user `kali`, save the archive as `/home/kali/Downloads/hermes-pg-stage33j-ack-recovery.zip`, verify its archive hash against ChatGPT's value, extract to `/home/kali` (directory must not already exist), then:

```bash
cd /home/kali/hermes-pg-stage33j-ack-recovery
sha256sum -c SHA256SUMS
python3 -m unittest -v test_stage33j
python3 -m py_compile stage33j.py test_stage33j.py
for f in *.sh; do bash -n "$f"; done
/home/kali/.hermes/node/bin/node --check reconcile33j.cjs
bash 61_preflight.sh
bash 62_run_once.sh
bash 63_verify_only.sh
```

## Expected markers

- `STAGE33J_READ_ONLY_PREFLIGHT_OK`
- `STAGE33J_LIVE_FUNCTION_EXTRACTED_AND_VERIFIED`
- `STAGE33J_SEPARATE_ROLES_AND_EXCLUSIVE_CANARY_PREFLIGHT_OK`
- `STAGE33J_ISOLATED_DIALOG_CREATED id=task_...`
- `STAGE33J_REAL_DB_TEST_REPORT_WRITTEN_NO_SDK`
- `STAGE33J_ACTUAL_JS_ACK_LOST_REPLY_RECOVERED`
- `STAGE33J_ISOLATED_OUTBOX_CLEANED_CANARY_UNCHANGED`
- `STAGE33J_REAL_DB_LOST_ACK_RESPONSE_RECOVERED_NO_SDK`
- `STAGE33J_ONE_SHOT_EXECUTION_COMPLETE`
- `STAGE33J_VERIFY_ONLY_CLEANLY_CLOSED`

## Guardrails

- No changes to `opencode.json`, plugins, systemd, Secure Tunnel, PostgreSQL schema/roles, production canary, production durable state
- No SDK calls or code execution based on test message body
- No `claim_outbox` across general queue and no unfiltered PG plugin
- `bridge_chatgpt` creates the isolated dialog, `bridge_agent` claims/reports/ACKs only that dialog
- `task_id`, delivery ID, lease UUID never appear in an executable shell command or log; lease token stays in mode-600 fixture
- Abort if pinned plugin/config hashes or canary invariants change
- After first DB mutation any error is a partial test; **never retry `62_run_once.sh`**

This tests the observed timeout/ACK recovery path and the lost-ACK-response condition, but **does not test actual long SDK runtime, lease expiry beyond 900s, concurrent DB failure or production arbitrary-task dispatch.** Those require separate bounded integration work.
