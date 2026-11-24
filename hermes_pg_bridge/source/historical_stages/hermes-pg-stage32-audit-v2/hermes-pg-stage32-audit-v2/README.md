# Stage 32b — read-only security audit repair

Previous Stage 32 failed because the canary query selected directly from `bridge.messages` and `bridge.outbox`, even though `bridge_app` has deliberately **no SELECT** on these tables. This is an audit bug, **not** a database permissions issue.

This replacement is a standalone, new-directory audit. The only functional change is the canary query: it invokes `bridge.dialog_metadata` and `bridge.delivery_status`, both granted in the existing PostgreSQL schema, and returns aggregate status without message bodies. Four messages are expected from the completed Stage 31; if `last_seq != 4` it fails closed rather than returning a misleading delivery count.

No SQL migration, GRANT, schema changes, message sends, tool calls or service modifications. The transaction is still `SET TRANSACTION READ ONLY`. AppImage variables are removed for the one subprocess as in the original. `32_run_readonly_audit.sh` is unchanged.

Run only after checking the outer package SHA256 and each file in SHA256SUMS. This package is authored by ChatGPT; agent should not edit code.
