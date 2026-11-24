# Hermes PG v2 — Stage 33D isolated actor-bound DB roundtrip

The existing production services, canary, plugin configurations, SSH ports, MCP,
Secure Tunnel, PostgreSQL roles/schema and permissions are **not changed**.

This package tests an isolated **database-only** actor roundtrip:
ChatGPT actor creates a new task and writes seq 1, Agent actor claims+ACKs the
outbox and writes seq 2, ChatGPT actor reads it, and then tombstones the **new**
task, canceling the pending seq 2 ChatGPT outbox. An existing canary remains at
`last_seq=4`. The MultiTool SDK is **never called**. This is not yet a test of
running the actual MultiTool plugin under the new role.

Preconditions: Stage 33C adapter pinned SHA-256, Stage 33B separate credentials,
shadow SSH service on localhost:15433, exactly one hard-filtered PG canary
plugin, and active Stage 31 one-shot Secure Tunnel. The script fails before
writes if a different/unfiltered PG plugin is present or if the new task's
request ID already exists. **Do not retry `36_run_once.sh`** after any failure:
use only `36_verify_only.sh` and return diagnostic markers.

Files contain no passwords, addresses, SSH keys or API keys. The package does
not require `PG_V2_SSH_TARGET` or GitHub.

Known limitation: two separate DB passwords remain readable by the same Linux
`kali` user. We must isolate them before permanent rollout. Also, SQL reports
already in the database may have pending ChatGPT outbox receipts; fixing the
consumer-specific acknowledgment path is a later explicit change.
