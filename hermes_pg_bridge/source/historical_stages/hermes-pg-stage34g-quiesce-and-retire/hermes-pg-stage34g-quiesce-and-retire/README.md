# Hermes PG v2 — Stage 34G, quiesce and retire the completed managed pilot

This is a **two-phase package**, written by ChatGPT, for the first complete Stage 34F-C managed roundtrip. Execute under OS user `kali`; MultiTool agent executes exact commands, **does not write code**. No Git bridge, SQL migration, SDK prompt, MCP/tunnel change, or deletion of messages/state.

## Grounded expected state, not inferred

Pilot `task_bddaadef89b7458fb7f8cf437dc0f023` is the **only** managed row and has `dispatch=true`, `policy_version=1`, `version=2`, `last_seq=2`. Message `seq=1` (ChatGPT task) and `seq=2` (agent report) are both `delivered`; `seq=2` body is exactly `PGV2_MANAGED_PILOT_ROUNDTRIP_OK_20261123`. The old canary `task_f2c64cfdf7894537b35a19a6def977c6` stays at seq=6, deliveries seq=5/6 delivered. MT config SHA256 is `4b32ac0fe83c902f5a7e2e5a749b104506987e1380aadc863ff15ca57b7786c3`, eight plugins. Seven-plugin baseline SHA is `e5320f6e971073d5d2c43a64ce85d165cb49dc9a94e6cf7806009ebc2b59ded1` and must match the preexisting `stage34fb/previous-opencode.json` byte-for-byte.

## Phase A — disable dispatch and revoke one exact gate (while MT can stay open)

1. `99_readonly_preflight.sh` confirms actual two actor roles, exactly one managed pilot, 42 functions, four tables, exact report, both deliveries, zero `inflight`, correct plugin pins, and the private write gate.
2. `100_disable_policy_and_revoke_gate_once.sh` durably writes a no-retry journal **before** a single `bridge.cg_set_managed_dispatch(PILOT,false,1,2)` under real `bridge_chatgpt` authentication. SQL transaction must commit and read back `policy_version=2`, `version=2`. Only after an independent read confirms the disabled policy, the exact 0600 regular write-arm file `/home/kali/.config/bridge-pg-v2/managed-writes-stage34d-armed` is unlinked and the parent directory fsynced.
3. `101_verify_quiesced.sh` checks that dispatch is off, the agent sees no dispatchable tasks, no write-arm exists, both deliveries remain delivered, state `inflight={}`, and no historical data was removed.

**Do not repeat 100 under any circumstances**, even if the COMMIT response is lost. If the result is uncertain, inspect `107_inspect_policy_readonly.sh` and `104_status_readonly.sh`. Consult ChatGPT. Only if PostgreSQL is independently confirmed disabled at policy_version=2 may `106_revoke_gate_after_confirmed_db_only.sh` be executed; it **does not retry SQL**. If the policy stayed enabled or changed unexpectedly, stop for a new ChatGPT correction.

## Phase B — prepare and separately arm the retirement handoff

1. `102_prepare_inactive_retirement.sh` confirms Phase A, creates a private copy of the current eight-plugin config and a byte-exact seven-plugin draft and installs a **static, INACTIVE** systemd user unit `hermes-pg-stage34g-retire.service`. It **does not** alter live config or close MT.
2. **Only on the owner's instruction** run `103_arm_after_owner_ready.sh`. It starts a one-shot worker waiting up to 900 seconds for all MultiTool/gigatool/opencode processes to exit. The worker will not kill processes. When no processes/unknown PIDs exist for 12s and all DB, state, SHA, gate and service checks pass, it atomically replaces `/home/kali/.config/gigatool/opencode.json` with its byte-exact seven-plugin baseline, preserving the canary and six other plugins.
3. The owner saves work and closes **all** MultiTool windows, does not reopen before verification.
4. Run `104_status_readonly.sh`, then `105_verify_after_handoff.sh`. Only after the latter succeeds, reopen **one** MT process. Check the old canary log for new idle ticks, if desired. History, stage34e state and the eight-plugin backup stay on disk.

If the worker times out or the final outcome is uncertain, **do not repeat arm** or manually edit config. Show `104_status_readonly.sh` + `systemctl --user status hermes-pg-stage34g-retire.service --no-pager` to ChatGPT. The exact SHA of the live config distinguishes the pre/post state. The old Stage34FB rollback helper must **not** be used after this pilot, because its guard assumes the pre-dispatch database state.

## Guarantees and limits

- Only one SQL business write, with optimistic policy and dialog version checking; no SQL schema updates.
- Removes only one verified private capability file, never wipes the state directory or report.
- Restores only the previously saved byte-for-byte seven-plugin config after all MT processes have exited; no silent plugin in-memory unload.
- Existing Secure Tunnel (18773), legacy canary MCP, PostgreSQL/SSH forward, and taskboard stay as they are.
- `AGENT` and `ChatGPT` have different DB logins; physical OS account and private secrets have **not** been separated yet.
- Successfully returning to seven plugins means **managed processing is intentionally stopped**. A future general managed MCP rollout must provide an authenticated interface and a separately reviewed agent dispatcher; do not re-enable old pilot by hand.
