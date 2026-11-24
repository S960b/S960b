# Stage 37B2F2: corrected historical verify after rollback

This package closes the historical runtime-import-probe stage **without rerunning** one-shot commit/verify/restore commands. Requires Stage 37B2F1 audit evidence to remain present. The previously installed script stage37b2f.py is NEVER modified. No app restart, SQL write, SDK call, config edit, file write to home, permit or dispatch change.

The old live verifier incorrectly called `set(mt)` where `mt` is a sequence of `[pid, start_ticks]` pairs; that raises a TypeError and hides a valid proof. The second regression masked STOP codes from a different module. Corrected functions are tested, but after rollback the original process is dead; the historical receipt is audited against SHA-pinned source, time bounds, private-file permissions, and exact rollback status. **This is consistent historical evidence, NOT a fresh live PID verification and NOT proof that runTick executed.** Historical receipts are not unforgeable by a same-user local process.

Run from Kali with no user terminal `set -e` at top level. In a fresh shell: `bash <unpacked>/stage37b2f2/480_audit_saved_proof_only.sh`. On STOP do not repeat; return markers. Next research track: uncertain PostgreSQL claim resolution, with no live crash until scoped safety proof exists.
