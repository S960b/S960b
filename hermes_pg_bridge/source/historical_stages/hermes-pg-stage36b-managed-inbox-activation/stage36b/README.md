# Hermes PG v2 — Stage 36B: register and run **one** existing managed Inbox task

## Purpose and the confirmed failure

The `pg` MCP successfully created a managed dialogue and committed message `seq=1`, but the agent side does not poll it: only the pinned canary Stage33I plugin is registered among seven `opencode.json` plugins. The Stage34E managed plugin is unregistered, and the Stage34D managed CLI write gate was revoked during Stage34G shutdown. Registering a plugin alone is therefore insufficient to complete an SDK roundtrip.

This package:

1. Pins existing seven-plugin config hash `e5320f6e971073d5d2c43a64ce85d165cb49dc9a94e6cf7806009ebc2b59ded1`, Stage34EB candidate, Stage34D CLI, canary source, MCP runtime35C, Stage36 scoped write permit, and exact pending PostgreSQL task.
2. Prepares a private **eighth-plugin draft** for **only** `task_b6c26aa18220412bafdbf32393f16568`, retaining all seven existing entries. It does not alter plugin code.
3. **Only after all MultiTool windows are closed**, atomically updates config and the MCP write permit to the new config SHA. The permit still has `allow_create=false`, exactly one task ID, and the **same original expiry**.
4. Installs a temporary Stage34D managed CLI gate (`0600`) for the existing agent. A persistent systemd-user timer removes **only the exact gate file** after 20 minutes. PostgreSQL dispatch already is true; the package never sets/changes it or inserts tasks/reports.
5. User launches one fresh MultiTool instance. The registered managed plugin can pick up **the existing seq=1**. Do **not** send a second task or manually append a report.

**Limitations:** The Stage34D CLI's gate is an OS-user capability, not independently task-ID scoped. The registered plugin and PostgreSQL policy narrow normal processing to one managed task; an independent malicious local process under `kali` could invoke the CLI while that gate exists. This is a tightly bounded test, **not** a production-grade permission model. The timer revokes the gate; it does **not** disable database dispatch. The scoped `pg` permit will expire on its original timestamp (about 23:44 Moscow time), without extension.

## IDs and pinned state

- New task: `task_b6c26aa18220412bafdbf32393f16568`, `version=1`, `policy_version=1`, `dispatch_enabled=true`, `last_seq=1`, `next_actor=agent`.
- Task `seq=1`, `message_id=pgv2-stage36-task-20261123-b6c26aa1-v1`, expected **pending, attempts=0** before activation.
- Expected exact agent report: `PGV2_STAGE36_E2E_ACK_20261123_B6C26A` with deterministic `message_id=pgv2-mg-b6c26aa18220412bafdbf32393f16568-1-report`.
- Old managed pilot `task_bddaadef89b7458fb7f8cf437dc0f023` stays disabled (`last_seq=2`, policy 2), canary `task_f2c64cfdf7894537b35a19a6def977c6` stays seq6.
- Seven existing plugins remain otherwise unchanged, including the live canary. No Git or legacy side channel is used.

## Step 1: install and prepare (MultiTool can remain open)

Extract ZIP to a **new temporary directory** (e.g. via Python zipfile) and call

```bash
bash <extracted>/stage36b/310_install_prepare_once.sh
```

This installs to `/home/kali/hermes-pg-stage36b-managed-inbox-handoff` and prepares only private draft files. It calls **32 offline tests** first. Required result: `STAGE36B_PREPARE_COMPLETE_NO_LIVE_WRITES`, then `STAGE36B_INSTALLED_PREPARED_NO_CONFIG_CHANGE`. If STOP, no rerun. Exact paths, hashes, SQL versions and permit expiry are rechecked against live state, never assumed.

## Step 2: operator closes MultiTool and commits once

The owner saves work and closes **ALL** MultiTool/gigatool/opencode windows and processes. Then, from a regular Kali terminal **outside MultiTool**:

```bash
bash /home/kali/hermes-pg-stage36b-managed-inbox-handoff/311_commit_when_multitool_closed.sh
```

The script refuses to commit while MultiTool-related processes are present, without killing anything. It journals the one-shot attempt before any live changes, installs the 20-minute persistent auto-revoke timer, then performs guarded atomic file replacements and creates the temporary CLI gate last.

Success requires `STAGE36B_EIGHTH_MANAGED_PLUGIN_REGISTERED_ONE_TASK_ONLY`, `STAGE36B_MCP_PERMIT_REBOUND_EXACT_TASK_NO_EXPIRY_EXTENSION`, and `STAGE36B_NOW_OPEN_MULTITOOL_TO_PROCESS_EXISTING_SEQ1_ONCE`. On failure **do not rerun commit**. Check `312_status_readonly.sh` and share only markers.

## Step 3: reopen MultiTool and collect

Open **one** MultiTool instance so it loads the new plugin. It should process the already queued seq1. Do not send any more tasks. After it has run, the agent may safely execute:

```bash
bash /home/kali/hermes-pg-stage36b-managed-inbox-handoff/315_progress_readonly.sh
```

This checks the live PostgreSQL task/report and local bridge state without printing message bodies or secrets. If report seq2 is present and exact, ChatGPT will verify via `pg`, ACK seq2 with an exact SHA256, disable dispatch by version check, and only then request permit and CLI-gate revocation.

## Safeguards and rollback

- **Emergency**: `313_emergency_revoke_gate.sh` only deletes the exact guarded local gate; never touches DB, config or plugin state. It may interrupt an in-flight report. Timer automatically calls this after 20 minutes and persists across user systemd restart/login.
- `314_rollback_before_claim.sh`: only when **ALL MultiTool processes are closed**, `seq1` remains `pending` with zero claims, no report exists, and no managed inflight. It revokes CLI gate, restores byte-exact seven-plugin config, and restores original scoped MCP permit. **Never** run after a task claim or report. Database dispatch remains true; ChatGPT must disable it separately.
- `312_status_readonly.sh` is safe after errors and expiry; does not mutate anything.
- A persistent marker prevents repeating one-shot `commit`, even after a partial failure; ChatGPT must inspect state before authoring recovery.

No manual edits, no SQL DDL, no service/tunnel changes, no db task writes, no OAuth changes, no secret output, no Git bridge.
