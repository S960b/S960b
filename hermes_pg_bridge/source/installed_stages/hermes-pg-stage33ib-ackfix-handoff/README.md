# Hermes PG v2 — Stage 33I-B: exclusive ACK-fix cutover

## Scope, evidence and limitations

Stage 33I-A has already produced and independently verified **unregistered** candidates:

- `/home/kali/hermes-pg-stage33i-ackfix/mt-pg-inbox-agent-stage33i.mjs`, SHA256 `220da977562f9c0763d6b5d5640f686f54f041d357ed397c8482c0d4363f525c`.
- `/home/kali/hermes-pg-stage33i-ackfix/stage33i_agent_cli.py`, SHA256 `4f0d2431342a389543e33789066b01e3166187b95848604c5a6c5c6a99e5095e`.
- Installed Stage33F plugin SHA256 `3535ae7bd5bb0da3e8574afcab299118dac744bc851c4bcb90b8656acd8b3ea7`.
- Current `opencode.json` SHA256 `a8f11a25173b5e7f1290d97b4aedfaea24403256fa09c16144093e0f9d297cb3`.

Stage 33I-B changes **only** the plugin path in one item of the seven-item `~/.config/gigatool/opencode.json`, retaining all other plugin entries and options. It does not move, reset or rewrite `~/.local/state/mt-pg-inbox-agent-stage33f/state.json`. New candidate's internal CLI points to the new actor-bound CLI and uses the exact same durable state directory. No Git bridge; no PostgreSQL or SDK writes; no tunnel, role, schema or service changes except installing and starting a one-shot *handoff* service.

Prerequisites: Stage33H both canary receipts `seq5=delivered`, `seq6=delivered`, last_seq=6, current durable state `seen[canary]=5, inflight={}`, role-bound SSH 15433 / MCP 18773 / Secure Tunnel services active. The SQL-read-only guard checks both roles at commit time. The systemd handoff requires **all** Kali-owned MultiTool, Gigatool, OpenCode and related Node/Bun processes to be absent for 12 consecutive seconds before writing the config. If any persist, the handoff is blocked and config is unchanged. It does **not kill** them. The owner should save running work and close all windows manually. No automated termination.

## Step 1: agent installs an INACTIVE one-shot handoff

User downloads archive into `/home/kali/Downloads` and verifies exact SHA provided by ChatGPT. Under user `kali`:

```bash
set -Eeuo pipefail; set +x; umask 077
ROOT=/home/kali/hermes-pg-stage33ib-ackfix-handoff
cd "$ROOT"
sha256sum -c SHA256SUMS
python3 -m unittest -v test_stage33ib
python3 -m py_compile handoff33ib.py test_stage33ib.py
for f in *.sh; do bash -n "$f"; done
bash 56_install_inactive_handoff.sh
bash 58_status.sh
```

Expected:
- `STAGE33IB_PRIVATE_BACKUP_AND_FIXED_DRAFT_READY`
- `STAGE33IB_SEVEN_PLUGINS_ONLY_ONE_ENTRY_CHANGED`
- `STAGE33IB_CANDIDATE_CLI_AND_JS_HASH_PINNED`
- `STAGE33IB_NO_RUNTIME_OR_DB_CHANGE`
- `STAGE33IB_HANDOFF_UNIT_INSTALLED_INACTIVE`

Stage 33I-B does not register the candidate in Step 1. Do not run `56_install_inactive_handoff.sh` twice.

## Step 2: user consents to closing ALL MultiTool processes

Only after agent has reported Step 1 success, the owner saves current work and approves closing MultiTool. Agent runs **once** from the package directory:

```bash
bash 57_arm_handoff_when_owner_ready.sh
```

Marker: `STAGE33IB_HANDOFF_ARMED_WAIT_FOR_USER_TO_CLOSE_ALL_MULTITOOL_WINDOWS`. Owner closes all windows. The systemd user unit (linger already enabled) performs the handoff after all MT processes exit; running from the agent is safe because the worker does not rely on the agent remaining open.

If the worker times out because a process stays alive, **stop**; do not kill tasks or re-arm without a new ChatGPT repair package. The worker records a `blocked` receipt and the live config remains unchanged.

## Step 3: verify from ordinary Kali terminal, with MT still closed

```bash
cd /home/kali/hermes-pg-stage33ib-ackfix-handoff
bash 58_status.sh
bash 59_verify_after_handoff.sh
```

Expected receipt `outcome=committed`, `code=ACKFIX_PLUGIN_REGISTERED`, and:
- `STAGE33IB_ACKFIX_PLUGIN_CONFIG_ATOMICALLY_INSTALLED`
- `STAGE33IB_PREVIOUS_DURABLE_STATE_UNCHANGED`
- `STAGE33IB_SEVEN_PLUGIN_ACKFIX_INSTALLED_GIT_ABSENT`
- `STAGE33IB_SEQ5_AND_SEQ6_DELIVERED_UNCHANGED`
- `STAGE33IB_VERIFY_COMPLETE_NO_SDK_OR_NEW_MESSAGES`
- `STAGE33IB_VERIFY_SUCCESS_POST_CONFIG_SWAP`

After successful verification the owner may open **one** MultiTool window and inspect `plugin loaded`, `tick_ok dispatched=0`, and exclusion of Git and Stage33F plugin. Agent then reports sanitized logs and SHA values. No new task is sent in Stage 33I-B. A negative result should not be overridden by restarting or resetting locks.

## Rollback (only on a separate explicit owner instruction)

`bash 60_rollback_when_all_mt_closed.sh` restores only the private config backup, and ONLY if all MT processes are absent, both DB receipts remain delivered and canary seq stays exactly 6. The old plugin contains a known reconciliation bug; reverting is a temporary safety recovery, not the preferred production mode. This rollback never changes or deletes durable state. No automatic rollback after ambiguous outcomes.

## Further work

The plugin now repairs a proven answer after a transient/timeout ACK failure. Further production development still must address lease expiry for SDK work longer than 3600 seconds, ChatGPT-side general report ACK, role-bound arbitrary dialogue operations, credential isolation and VPS deployment. This stage is a **canary-only** cutover, not production authorization to handle arbitrary tasks.
