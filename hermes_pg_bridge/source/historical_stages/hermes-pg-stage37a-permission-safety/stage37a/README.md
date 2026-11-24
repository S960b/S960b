# Stage 37A — durable capability revocation and expiry watchdog

**Source basis:** Stage 35C `runtime35c.py`, Stage 36 `stage36.py`, Stage 36B `stage36b.py`, and Stage 34D `managed_agent_cli34d.py`. Exact 36B failure: `302_revoke_permit.sh` refused a changed `config_sha256` after the 7→8 plugin transition; agent and MCP gates were then manually removed. Stage 36 E2E completed, both receipts delivered, dispatch=false.

## Safety model

**Never issues write capability.** Never modifies config, DB, plugin list, MCP runtime, tunnel, dispatch, or task history. Never touches old quarantined backups. This package only installs a local maintenance CLI and user-systemd timer; and, when explicitly requested or when a capability is stale, quarantines exactly TWO named *active* files:

- `/home/kali/.config/bridge-pg-v2/managed-writes-stage34d-armed` (agent CLI gate)
- `/home/kali/.config/bridge-pg-v2/managed-mcp-write-permit35c.json` (ChatGPT MCP permit)

Emergency `revoke` needs **no** config hash, database, MCP server, tunnel, or permit JSON schema. Under a trusted owned directory, uses `dir_fd` + `rename` + `fsync`, never follows symlinks, retains contents in private same-directory `.revoked-stage37a-*.bak` backups, confirms both active names gone and is idempotent. Never share these backups: they can contain private content. A directory unexpectedly occupying either active filename causes STOP, not a misleading success.

Periodic watchdog (2 min) has a stricter purpose: if permit absent/expired/unreadable/invalid/config-sha-mismatched it quarantines both active files. If permit is valid but an agent gate is unsafe, not for exactly one scoped task, malformed, or older than 20 min, it quarantines **only** the gate. A valid task-scoped permit is not modified. Independent existing 36B 20-minute timer may still exist but is not relied upon.

Limitations: this is a **fail-closed compatibility controller**, not a new issuance API. The `runtime35c.permitted()` and Stage34D ACL code are unchanged. A malicious process with the same `kali` UID can bypass discretionary file-based controls; use a separately isolated account/service for production. During a future legitimate issuance a mismatch between config SHA and permit causes watchdog revocation rather than silent re-binding. Future issuance workflow must be a separate reviewed stage.

## Installation when both gates already absent

1. Download the single Stage37A ZIP. On Kali run the supplied `370_install_and_enable_watchdog.sh` from the extracted `stage37a` directory, after SHA check.
2. It performs **offline Python tests**, source integrity, syntax checks, and a read-only preflight requiring the current eight-plugin state and **no active write capabilities**. It installs to `/home/kali/hermes-pg-stage37a-permission-control` (code mode 0600) and a user-systemd timer, runs a single watchdog pass, and verifies closure. The guard is installed without restarting MultiTool or MCP.
3. Expected `STAGE37A_EIGHT_PLUGIN_BASELINE_READONLY_VERIFIED`, `STAGE37A_WATCHDOG_INSTALLED_2MIN_20MIN_GATE_LIMIT`, `STAGE37A_READY_FOR_OPERATOR_AUDIT`, plus `STAGE37A_PERMIT_PRESENT False`, `STAGE37A_CLI_GATE_PRESENT False`.

## Operational commands, any time AFTER install

- `bash /home/kali/hermes-pg-stage37a-permission-control/372_status_readonly.sh` — no writes, no secrets, no DB.
- `bash /home/kali/hermes-pg-stage37a-permission-control/371_emergency_revoke.sh` — always closes both local active write capabilities regardless of config drift; STOP only on untrusted filesystem conditions.
- `bash /home/kali/hermes-pg-stage37a-permission-control/373_watchdog_once.sh` — normal periodic rules, no re-arm.
- `bash /home/kali/hermes-pg-stage37a-permission-control/374_disable_watchdog_timer.sh` — disables the NEW watchdog only, without restoring any write permit. This is the rollback for timer installation. Does not alter old Stage36B timer or write capability.

## What this explicitly does NOT prove

- No test of PostgreSQL write-denial is performed by this package; Stage36 had independently confirmed no active permit and MCP health.`managed_health` from ChatGPT may be used to confirm `write_permit_present=false`.
- Timer cadence is approximate and requires `systemd --user` to be active. MCP itself rejects expired permits at request time; this timer additionally cleans up files.
- It does not solve isolated agent CLI permissions, automatic new dialogues, queue retry semantics, duplicate reports, or crash recovery. These are later stages of 37.
