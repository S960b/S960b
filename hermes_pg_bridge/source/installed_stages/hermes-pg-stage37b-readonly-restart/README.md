# Stage 37B1 — read-only MultiTool restart baseline

## What this proves

After 37A and the successful Stage36 E2E, verify that a **manual clean restart** of MultiTool preserves both PostgreSQL receipts and local managed Inbox cursor without duplicating the existing completed report. This is **not** a test of in-flight crash recovery or lease expiry: those require Stage37B2/37C.

The scripts only read PostgreSQL through the pinned `bridge_chatgpt` client in a `SET TRANSACTION READ ONLY` connection. They do not change the database, run CLI gate, issue grants, write opencode.json, restart services, or kill processes. Only stage-local private checkpoint files are written. The operator must close/reopen MultiTool by hand.

## Exact procedure

1. On Kali as user `kali`, while MultiTool is **open**, verify the downloaded ZIP SHA256, extract, and execute `bash stage37b/380_install_readonly_restart.sh`. One-shot; do not retry if it fails after installation. Send `STAGE37B1_*` markers.
2. With MultiTool still open, run: `bash /home/kali/hermes-pg-stage37b-readonly-restart/381_before_restart.sh`. If it emits `STAGE37B1_NOW_CLOSE_ALL_MULTITOOL_WINDOWS_AND_CONFIRM_QUIESCENCE`, proceed.
3. **Manually exit every MultiTool window and process.** Do not issue kill commands via this package. In a separate ordinary Kali terminal, run: `bash /home/kali/hermes-pg-stage37b-readonly-restart/382_confirm_quiesced.sh`. If process remains, it fails closed; close remaining windows and re-run this *read-only phase* (no checkpoint was created).
4. After successful quiescence confirmation, open **exactly one** MultiTool instance. Run: `bash /home/kali/hermes-pg-stage37b-readonly-restart/383_verify_reopened.sh`. Return all `STAGE37B1_*` markers. There are no send/ACK calls.
5. Any time: `bash /home/kali/hermes-pg-stage37b-readonly-restart/384_status_readonly.sh`.

## Important safety notes

- Stage37B1 must not run with a live task, nonempty inflight, enabled dispatch or any active local permit/agent gate. It refuses to proceed.
- This is a **clean** application restart only, not SIGKILL/crash recovery. Next stage will test those separately using a fresh scoped dialogue.
- `STAGE37B1_STOP_*` means stop and share the markers; never delete or rewrite checkpoints to force a pass. Do not re-run `380` after it succeeded.
- Scripts never modify the existing Stage36 conversation or old canary.
- Stage37A watchdog remains enabled, with no write permits. Any permit on either side aborts the snapshot.
