# Stage 37B2F1 — retrospective proof audit, read-only

Run once through an independent `bash` process under `kali`; does **not** rerun 461/462/463, does not restart MultiTool, invoke the SDK, call PostgreSQL or alter any file or service. Verifies preserved B2F receipt against pinned original B2F source, candidate SHA, one-path draft, commit/restore timestamps and active restored configuration. Prints no secret values or raw logs.

Interpret `STAGE37B2F1_HISTORICAL_IMPORT_EVIDENCE_CONSISTENT` as **strong consistent historical evidence**, not a live `/proc` PID verification (the process was exited on rollback). The file was written by a wrapper importing the candidate and then using `openSync('wx')` + `fsync`; the proof alone is not cryptographically unforgeable and cannot prove `runTick` ran. The user's contemporaneous `/proc` PID/start-tick observation materially strengthens the conclusion.

Current write permit must be absent; no database write requests or dispatch activation. A STOP means return marker and do nothing else.
