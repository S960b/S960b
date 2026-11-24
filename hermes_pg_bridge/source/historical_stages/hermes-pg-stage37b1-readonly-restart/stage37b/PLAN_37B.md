# Stage 37B roadmap

37B1 = safe operator-managed MultiTool restart with a QUIESCED completed dialogue. **No new tasks, no write permits, no changes to systemd, PostgreSQL, or plugin config.**

37B2 = **separate later stage**, controlled single fresh dialogue to test recovery of a leased-but-unacknowledged task. Not included in 37B1 because issuing new permits, enabling dispatch and fault-injecting a live leased task requires a separate scoped safety review and explicit operator instructions. It must not be inferred from 37B1 success that lease recovery has been proved.

37B3 = evaluate duplicate-report avoidance in presence of ambiguous agent writes, crash before ACK, stale inflight reservation and lease expiry, after 37B2.

37B1 criteria: full quiescence of all MultiTool processes, restart with entirely new processes, private local `seen` marker retained, no inflight, byte-exact 8-plugin configuration unchanged, existing task/report receipts still delivered with one attempt each, `dispatch=false`, MCP and agent write capabilities absent and 37A watchdog active.
