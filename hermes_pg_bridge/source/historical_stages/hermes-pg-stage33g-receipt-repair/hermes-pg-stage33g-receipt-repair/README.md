# Hermes PG v2 — Stage 33G: exact canary receipt reconciliation

This package fixes **one already-completed** PostgreSQL test delivery, not the SDK runtime.
It does not re-claim or re-send `seq=5`, does not run MultiTool, does not modify plugins or the MCP,
and does not acknowledge `seq=6` (its ChatGPT-directed receipt remains pending).

## Facts and preconditions

- fixed canary: `task_f2c64cfdf7894537b35a19a6def977c6`
- `seq=5`: exact `pgv2-stage33f-sdk-task-v1`, `chatgpt → agent`
- `seq=6`: exact `pgv2-stage33f-sdk-report-v1`, `agent → chatgpt`, body exactly `PGV2_ROLE_BOUND_MT_SDK_ACK_20261122`
- dialog active, `last_seq=6`, `version=6`, next actor ChatGPT
- existing `seq=5` lease owned by `mt-pg-inbox`; its UUID is used only *inside* the PostgreSQL transaction
- existing PostgreSQL `bridge.ack_outbox` function is called only after all invariants are verified
- current remote user has passwordless root SSH access and can use `runuser -u postgres`; `PG_V2_SSH_TARGET` provided privately

The SQL uses a single transaction with row locks on the dialog and the exact outbox row.
Unexpected state means **zero writes**. An already-delivered row is a no-op.
No new functions, grants, roles, tables, service changes or SDK restarts.

## Run order (under user kali)

```bash
sha256sum -c SHA256SUMS
python3 -m unittest -v test_stage33g
for f in *.sh; do bash -n "$f"; done
bash 45_readonly_preflight.sh
bash 46_ack_exact_once.sh
bash 47_verify_only.sh
```

`46_ack_exact_once.sh` runs preflight itself, then the single guarded transaction, then post-verification. Do not rerun it after any uncertain SSH result; run only `47_verify_only.sh` and seek reconciliation guidance. A failed preflight does not authorize manual ACL changes or lease resets.

**Caveat:** this repairs the database delivery status only. The plugin's general-purpose reconciliation path still needs to ACK a confirmed response **before** removing its durable `inflight` state. That plugin fix must be separately developed and tested, then activated using an exclusive process handoff. Increasing the SDK timeout alone does not solve receipt loss.
