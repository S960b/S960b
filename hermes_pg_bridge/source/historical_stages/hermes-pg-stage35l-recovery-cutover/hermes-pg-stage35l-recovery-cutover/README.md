# Stage 35L — resume after successful 35J, rolled-back 35K

A single-stage production-tunnel cutover recovery, authored by ChatGPT. Agent executes the exact command only. No agent code changes.

## Evidence required before mutation

The previous Stage 35J local E2E is proven (`stage35j-passed.json`), Stage 35K attempted and rolled back (`stage35k-cutover-attempt.json`, `stage35k-rolled-back.json`), but the new tunnel never reached a success marker. Old tunnel (33FD) must be active and healthy (`18773/ping`, `18774/readyz`), managed MCP (35C/35G) must be stopped, and the new unit must not exist. Original 35JK source/launcher, 35G patched systemd runtime, 35HI and 7 plugin fingerprint must be pinned. DB-write-permit file must remain absent. The 35J proof is **reused**; no re-run of one-shot diagnostic and no `DELETE` retry. The observed 501 on DELETE remains a nonfatal cleanup limitation, not a successful session-delete proof.

## What the one-shot run does

1. Installs private package, checks SHA256SUMS, syntax/AST and offline tests.
2. Verifies prerequisites and rollback state; creates a distinct 35L one-shot marker **before any modifications**.
3. Secures only the owner-owned user systemd `~/.config/systemd/user` directory to `0700` if previous mode is historical `0775`. If `~/.config/systemd` is also `0775`, secures this immediate ancestor to `0700` first to prevent a group-write rename attack. Refuses symlinks, wrong owner, unknown modes; does not use recursive chmod. Leaves `~/.config` untouched.
4. Creates the original pinned 35JK **not-enabled** unit, starts pinned patched 35C MCP, waits for `18777/ping`, stops the old tunnel **before** starting the new tunnel with the same tunnel ID, then checks `18778/readyz` and no old-tunnel listener on `18774`.
5. Checks `18773/ping`, preserved seven plugins, no write permit, secure systemd directory, and marks `remote_plugin_check_pending` on success.
6. If cutover fails, first stops new tunnel and proves port `18778` closed; only then restores old tunnel and proves `18774/readyz` plus `18773/ping`. Stops managed MCP. Does not falsely mark success if rollback incomplete.

No DB writes, no managed dispatch arming, no changes to old unit or plugin settings, no external second tunnel ID. After local success, an actual ChatGPT-to-remote-MCP plugin read call is **still required**. The new unit is not enabled at boot, intentionally until remote verification completes.

## Commands

Extract verified archive and run exactly once as `kali`:
`bash hermes-pg-stage35l-recovery-cutover/190_install_and_resume_cutover_once.sh`

Nonmutating status check (repeatable):
`bash /home/kali/hermes-pg-stage35l-recovery-cutover/191_verify_only.sh`

Manual rollback **only if instructed by owner** and remote MCP check fails:
`bash /home/kali/hermes-pg-stage35l-recovery-cutover/192_owner_rollback.sh`

Do not run old 180, 121, or previously attempted 35J/K scripts. Do not print files with secrets or raw journal logs.

## Local and remote milestones

`STAGE35L_CUTOVER_NEW_18778_READY_MCP_18777_OLD_TUNNEL_INACTIVE`
`STAGE35L_CUTOVER_SUCCESS_REMOTE_CHATGPT_MCP_TEST_REQUIRED_NO_DB_WRITES`
`STAGE35L_INSTALL_COMPLETE_NEW_TUNNEL_ACTIVE_REMOTE_CHECK_REQUIRED`

On failure: `STAGE35L_CUTOVER_FAILED_AUTO_ROLLBACK_SUCCESS_OLD_ACTIVE` or a CRITICAL marker. Do not retry.
