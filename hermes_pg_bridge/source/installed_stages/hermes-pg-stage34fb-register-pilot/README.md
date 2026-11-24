# Hermes PG v2 — Stage 34F-B: register one disabled managed pilot in MultiTool

This package does **not** enable managed dispatch, create messages, arm the managed write gate, start or stop MultiTool, or modify PostgreSQL or the Secure MCP Tunnel.

## Exact pilot and current-state pins

- `task_bddaadef89b7458fb7f8cf437dc0f023`, registered, `dispatch_enabled=false`, `policy_version=0`, `last_seq=0`, title `Hermes PG v2 Managed Pilot Stage 34F (dispatch disabled)`.
- Fixed create request `stage34f_pilot_20261123_v1` and private `pilot-created.json` from Stage 34F-A.
- Original **seven-plugin** `opencode.json` SHA256 `e5320f6e971073d5d2c43a64ce85d165cb49dc9a94e6cf7806009ebc2b59ded1`.
- Candidate `mt-pg-inbox-managed-stage34eb-pilot-unregistered.mjs` SHA256 `b3679086583d77c96b6ed1c3dc7b1ed1656707dabba4d3ac74843fe64177d730`.
- Existing canary `mt-pg-inbox-agent-stage33i.mjs` SHA256 `220da977562f9c0763d6b5d5640f686f54f041d357ed397c8482c0d4363f525c` is unchanged.
- Managed CLI SHA256 `bf8cad69a02e71a70ec3377504c24a3fc2cb71a38f2c8c4a025abc587b774485`; managed write gate must be **absent**.
- Both PG actor logins verified via existing 15433 actor adapter. No need for manual `psql` or `.pgpass` port changes.

## Resulting configuration

A new **eighth** plugin is added, without modifying the seven existing entries:

```json
[
  "/home/kali/hermes-pg-stage34eb-pilot-candidate/mt-pg-inbox-managed-stage34eb-pilot-unregistered.mjs",
  {
    "dry_run": false,
    "tick_ms": 30000,
    "task_ids": ["task_bddaadef89b7458fb7f8cf437dc0f023"]
  }
]
```

**Warning:** This is an active-capable plugin, but the PostgreSQL dispatch policy remains disabled and its CLI write gate remains absent. No SDK work should be possible while these gates are intact. The existing canary plugin continues independently with its original `task_ids`. Managed and canary plugin state directories and file locks are distinct.

## Step 1 — agent installs inactive handoff

Download `hermes-pg-stage34fb-register-pilot.zip` to `/home/kali/Downloads/`. The outer SHA256 must match the value in the ChatGPT message. From the `kali` account (do not run as root):

```bash
set -Eeuo pipefail
set +x
umask 077
PKG=/home/kali/Downloads/hermes-pg-stage34fb-register-pilot.zip
ROOT=/home/kali/hermes-pg-stage34fb-register-pilot
test ! -e "$ROOT"
python3 -m zipfile -e "$PKG" /home/kali
cd "$ROOT"
sha256sum -c SHA256SUMS
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python -m unittest -v test_stage34fb
for f in *.sh; do bash -n "$f"; done
bash 89_install_inactive_handoff.sh
bash 91_status.sh
```

Expected `STAGE34FB_PRIVATE_BACKUP_AND_EIGHT_PLUGIN_DRAFT_READY`, `STAGE34FB_CANARY_AND_ONE_PILOT_BOUND_TO_DISJOINT_TASK_IDS`, `STAGE34FB_PILOT_DISABLED_NO_SDK_AND_MANAGED_WRITE_GATE_ABSENT`, `STAGE34FB_HANDOFF_SERVICE_INSTALLED_INACTIVE`. A `proposed-opencode.json` and a byte-for-byte backup (0600) are saved, but the **live** config remains SHA256 `e5320f6e…`.

## Step 2 — owner-approved cutover

Only after Step 1 succeeds and the user is ready to close **all** MultiTool windows:

```bash
cd /home/kali/hermes-pg-stage34fb-register-pilot
bash 90_arm_when_owner_ready.sh
```

The service will wait for all MultiTool processes owned by user `kali` to disappear for 12 consecutive seconds. It **never kills processes** and will not replace configuration if process identity is uncertain. It then rechecks role logins, the disabled pilot, exact canary deliveries, empty managed dispatch, private receipts and config hash immediately before an atomic replacement. If MultiTool does not fully exit within 15 minutes, it reports a blocked outcome; do not retry without reviewing the result.

After arm, **close all MultiTool windows** and use a normal Kali terminal (not a MultiTool terminal):

```bash
cd /home/kali/hermes-pg-stage34fb-register-pilot
bash 91_status.sh
bash 92_verify_after_handoff.sh
```

Expected `STAGE34FB_EXACT_ONE_DISABLED_MANAGED_PILOT_REGISTERED`, `STAGE34FB_EIGHT_PLUGINS_CANARY_AND_PILOT_DISJOINT`, `STAGE34FB_MANAGED_DISPATCH_STILL_DISABLED_AND_NO_TASK`, `STAGE34FB_POST_HANDOFF_VERIFIED`.

**Only then** reopen one MultiTool instance. After its startup and first tick, check:

```bash
cd /home/kali/hermes-pg-stage34fb-register-pilot
bash 94_verify_after_multitool_launch.sh
```

This checks that the managed plugin loaded and recorded a healthy idle tick, without printing message bodies or secrets. If the check is too early, report the status; do not modify the configuration.

## Recovery

`93_rollback_before_dispatch.sh` is a **separately approved** config-only rollback requiring **all MultiTool processes closed**, zero pilot messages, disabled pilot policy and absent write gate. Never execute after managed dispatch is enabled or any pilot message exists. Never repeat one-time installation if it partially failed; use `91_status.sh` and read-only checks.

## Next stage, NOT done here

Only after independent verification of the pilot plugin registration and its idle tick, prepare separate restricted MCP tools and enable the pilot policy under `bridge_chatgpt`. The first SDK task must be sent only after controlled explicit authorization and write-gate checks. The Secure Tunnel remains on the current fixed canary test MCP at this stage.
