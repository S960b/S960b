# Stage 36: controlled managed PostgreSQL roundtrip

This package does **not** call PostgreSQL write functions, change services, install an MCP server, or edit ChatGPT plugins. It creates a strictly scoped, expiring local permit that the existing `runtime35c.py` checks on each management write. The bridge_chatgpt login is reused; no new credentials or Git operations.

Operating sequence:

1. Operator runs `300_install_and_arm_create.sh` once. It pins the exact current 7-plugin configuration, requires the current new tunnel and MCP to be healthy, and checks the managed registry consists of just the old disabled pilot. It creates a 30-minute permit with `allow_create=true` and **no task IDs**. Only creation of an initially disabled managed dialogue is possible.
2. ChatGPT calls the `pg` plugin `managed_create` with **request_id** `pgv2-stage36-independent-e2e-20261123-v1` and **title** `PGV2 Stage36 Independent Managed E2E Canary`. Do not invent new IDs on uncertain outcomes; use `managed_create_status`.
3. Operator runs `301_scope_to_created_task.sh <exact_task_id>` from the confirmed result. It requires this exact new title, 0 messages, version 0, policy 0, disabled dispatch, and exactly two managed dialogues. It atomically replaces the permit with `allow_create=false`, the sole task ID, and a 60-minute expiry.
4. ChatGPT verifies status, enables dispatch using expected policy/dialogue versions, sends **one** inert canary to MultiTool, reads the report, ACKs using SHA-256, and disables dispatch while the scoped permit remains valid. Repeat ambiguous write actions only by checking *_status with the original IDs.
5. Operator runs `302_revoke_permit.sh` to remove the local write permit. Verify `managed_health.write_permit_present=false` and dispatch disabled. The new managed test dialogue remains registered for audit; no data deletion is performed.

**Failure or timeout:** Do NOT repeat the one-shot installer or invent a new request_id. `302_revoke_permit.sh` may be used for emergency revocation even if the permit has expired. Revoking before dispatch is disabled **does not** change the DB dispatch flag; ChatGPT must verify and, if needed, arrange a scoped recovery to disable it safely.

The pilot already in the registry (`task_bddaadef89b7458fb7f8cf437dc0f023`) is never selected for writes by this package. The completed fixed canary (`task_f2c64cfdf7894537b35a19a6def977c6`) is excluded.
