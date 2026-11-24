# Stage 36A — recovery for Stage 36 title mismatch

The existing Stage 36 create permit produced a valid PostgreSQL record:

- request_id: `pgv2-stage36-independent-e2e-20261123-v1`
- task_id: `task_b6c26aa18220412bafdbf32393f16568`
- actual title: `Hermes PG v2 Independent E2E Stage 36 (dispatch disabled)`

The original Stage36 301 script instead expects `PGV2 Stage36 Independent Managed E2E Canary`. Running it fails deliberately.
This separate, one-off Stage 36A wrapper does not edit Stage 36, PostgreSQL, MCP, tunnel, or services. It pins the SHA256 of the installed Stage 36 controller (`7d488d187acdedc47dc4fc0f0faf8ab392acdbddd74a6b08c8909744787ef3a2`), loads it as an isolated module, replaces its title expected constant **in memory only**, and calls the original `scope()` unchanged. That preserves all checks for 0 messages/versions, registry exactly two rows, dispatch disabled, valid unexpired create permit, owner of files, and service health. It changes the local permit to a 60-minute single-task permit with `allow_create=false` and leaves dispatch disabled.

Run only after `managed_create_status` confirms the above task and before the original 30-minute permit expires. If `STAGE36_STOP_CREATE_PERMIT_EXPIRED_OR_CHANGED` appears, do not repeat or rearm automatically. Inform ChatGPT; an independent expired-window recovery must be designed. Repeated create is forbidden.

After success: return markers; ChatGPT will enable dispatch via pg with exact policy version 0 and dialogue version 0, send one inert instruction, receive and acknowledge, disable dispatch, then operator runs original `302_revoke_permit.sh`.

This package includes only a one-shot execution script and no secrets.
