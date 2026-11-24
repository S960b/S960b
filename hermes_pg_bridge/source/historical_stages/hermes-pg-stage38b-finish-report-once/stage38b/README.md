# Stage 38B — one-shot completion of existing Stage 38 report

The old Stage38 permit and gate correctly expired and were quarantined by Stage37A. Do not revive them. This new package exclusively creates fresh capabilities with the **existing** Stage38 armer's hardcoded scoped policy, then posts one already-observed exact diagnostic report using the authorized Stage34D agent CLI. It does not repeat GET diagnostics, call SDK, edit PostgreSQL schema, change config, register plugins, restart services or open new tasks.

**Target**: `task_b6c26aa18220412bafdbf32393f16568`, `seq=5` awaiting agent, deterministic report `pgv2-mg-b6c26aa18220412bafdbf32393f16568-5-report`.

How it works:
1. Pin original Stage38 armer, deployed Stage34D CLI and its read module by SHA256.
2. Read existing report status and task/head using the actual agent CLI. Stop on unexpected state, stale head or any status error.
3. Check the existing 8-plugin configuration, managed plugin path, no unresolved claim intents, and active watchdog using the pinned Stage38 preflight.
4. Under the Stage37A file lock, exclusively create a scoped MCP permit and agent gate (max 60 and 20 minutes), without touching quarantined copies or extending any previous permit.
5. Check status again; append the **one preexisting report** using the same idempotency key. Check durable report status even if append result is uncertain; never retry with a new id.
6. On any failure *after opening privileges*, invoke the independent emergency revoker. If successful, **leave capabilities open for ChatGPT to ACK seq=6**, and revoke both immediately after ACK.

### Agent command

```bash
bash -c 'set -Eeuo pipefail; umask 077; cd /home/kali/Downloads; echo "<ZIP_SHA>  hermes-pg-stage38b-finish-report-once.zip" | sha256sum -c -; TMP=$(mktemp -d /home/kali/.stage38b-XXXXXXXX); trap "rm -rf -- \"$TMP\"" EXIT; python3 -m zipfile -e hermes-pg-stage38b-finish-report-once.zip "$TMP"; bash "$TMP/stage38b/520_finish_existing_report_once.sh"'
```

Only attempt once. If STOP, report output and do not retry; if success, notify ChatGPT, which will ACK and request emergency revoke. Do not submit report independently or change configuration.

Requirements: user `kali`, unchanged 8-plugin config SHA `f46c3d4c2a46…`, candidate SHA `06be5ea765f0…`, installed watchdog 37A active, correct existing CLI stage34D code and no active capability at the two known paths. Old quarantined files are left untouched.
