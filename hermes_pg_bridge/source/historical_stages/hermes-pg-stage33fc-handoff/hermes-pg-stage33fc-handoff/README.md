# Hermes PostgreSQL v2 — Stage 33F-C: safe handoff of MultiTool plugin configuration

**Goal:** retire the **old Git plugin** in `opencode.json` and replace the active PG `bridge_app` canary plugin with the **role-bound bridge_agent Stage 33F** candidate. Preserve the other six plugins and all other settings. Do this **only while every MultiTool process has exited**, after a quiet interval. Existing `bridge_chatgpt` test MCP on 18773 remains **local only and unarmed**. Do **not** attach it to OpenAI Secure Tunnel yet.

## Critical constraints

- There are **16 known MultiTool desktop/worker processes** in the preceding inventory. The agent is itself running inside MultiTool and cannot safely kill/restart itself from that chat.
- This package **never kills a process**. A one-time, disabled systemd user **oneshot** worker can be armed while the agent still runs. Then the **user must save work and close all MultiTool windows**. The worker waits for zero matching processes to remain for **12 continuous seconds** before modifying `opencode.json` atomically. If that does not occur before its 15-minute hard deadline, it stops without changing the config. The timer is only a safety deadline; no guarantee of completion is made.
- Do **not** start/reopen MultiTool until the service shows `handoff-result.json` with `"outcome":"committed"`. This can be checked from a standalone Kali terminal. If it says `blocked`, **do not** restart or rerun the worker; return logs for new instructions.
- All previous Stage 33F-B guards are re-run just before committing: original config SHA, plugin candidate SHA, current PostgreSQL canary `seq=4`, both distinct roles and Secure Tunnel still connected to Stage 31. No `seq=5`, no `claim`, no `ack`, no SDK prompt in this package.
- **The old Git MCP and Git Taskboard services are disabled by `40_arm_handoff.sh`**, consistent with the owner's explicit retirement instruction. No database or legacy files are deleted. The Git plugin is removed from the new config only once all MultiTool processes exit. The older `bridge_app` canary plugin is also removed.
- A Python config backup `original-private.json` and a proposed `pg-only-cutover.json` are stored with 600 permissions under `/home/kali/hermes-pg-stage33fc-handoff`. The live config is never overwritten without checking its original exact SHA.
- No claim of a **unique loaded runtime** is made merely from changing config. The following stage will verify that exactly one intended MultiTool instance loads the new plugin before arming the test write or attaching Secure Tunnel.
- A failure after config replacement should be treated as ambiguous; inspect `40_status.sh` and the actual config hash, **do not automatically rerun**.

## Operator sequence

**Agent executes install only**, while MultiTool is still open:

```bash
cd /home/kali/hermes-pg-stage33fc-handoff
sha256sum -c SHA256SUMS
python3 -m unittest -v test_stage33fc
for f in *.sh; do bash -n "$f"; done
bash 40_install_handoff.sh
bash 40_status.sh
```

Once the operator has saved ongoing sessions and explicitly accepts closing all MultiTool desktop windows, **agent executes** (still from MultiTool):

```bash
cd /home/kali/hermes-pg-stage33fc-handoff
bash 40_arm_handoff.sh
```

Expected `STAGE33FC_HANDOFF_ARMED_WAITING_FOR_USER_TO_CLOSE_ALL_MULTITOOL_WINDOWS`.

**User** saves work and closes **all** MultiTool windows, including the one carrying this agent. Don't force-kill sessions; if some orphan processes persist, return evidence and ask ChatGPT for a process-specific plan. Close only MultiTool windows; other apps/services, PostgreSQL, Secure Tunnel and SSH can remain running.

In a **standalone Kali terminal**, while MultiTool is still closed:

```bash
cd /home/kali/hermes-pg-stage33fc-handoff
bash 40_status.sh
bash 40_verify_after_handoff.sh
```

Do not invoke verify if status says `pending` or `blocked`: it is supposed to fail. On success, verify emits `STAGE33FC_PG_ONLY_CONFIG_VERIFIED_GIT_PLUGIN_ABSENT`.

Only **after** successful verification may the user open **one** MultiTool window for follow-up report. **Do not send seq=5**. Do not switch tunnel or create `armed-stage33fc` yet.

Rollback, *only before test `seq=5` and while ALL MultiTool processes are closed*:

```bash
cd /home/kali/hermes-pg-stage33fc-handoff
bash 40_rollback_before_seq5.sh
```

This restores the original 8-plugin config, but does **not** restart the old Git services. Never use it after seq=5 or while any MT instance is running.

## Safe evidence to return

- `STAGE33FC_PRIVATE_BACKUP_AND_PG_ONLY_CONFIG_PREPARED`
- `STAGE33FC_OLD_GIT_AND_CANARY_PLUGINS_REMOVED_FROM_DRAFT`
- `STAGE33FC_LIVE_CONFIG_NOT_CHANGED`
- `STAGE33FC_HANDOFF_UNIT_INSTALLED_NOT_ARMED`
- `STAGE33FC_LEGACY_GIT_SERVICES_DISABLED`
- `STAGE33FC_HANDOFF_ARMED_WAITING_FOR_USER_TO_CLOSE_ALL_MULTITOOL_WINDOWS`
- After shutdown: `STAGE33FC_PG_ONLY_CONFIG_ATOMICALLY_INSTALLED`
- `STAGE33FC_GIT_PLUGIN_REMOVED_AND_ROLE_AGENT_CANDIDATE_REGISTERED`
- `STAGE33FC_PG_ONLY_CONFIG_VERIFIED_GIT_PLUGIN_ABSENT`
- `STAGE33FC_NO_SEQ5_AND_SEND_NOT_ARMED`

Send only safe process metadata, service states and hashes, no private config or passwords. If the process inventory still shows residual MultiTool worker PIDs, don't kill them automatically.
