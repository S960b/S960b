# Bridge v2 / PostgreSQL — live sources

**Status (2026-11-28): running.** This directory is the current implementation of the
PostgreSQL-managed bridge between ChatGPT (via MCP) and the local MultiTool agent.
It records messages, delivers them with leases and acknowledgements, and controls
whose turn it is. It is not a copy of the private runtime.

## What runs

- MCP gateway `hermes-pg-stage35c-static-shadow.service` — `127.0.0.1:18777`, 13 tools.
  Assembled from `stage35v` (pin checks + JSON-RPC policy) → `stage35g` (patched
  runtime and static gate) → `stage35c` (tool implementations, Ed25519 signing).
- Taskboard `bridge-pg-v2-taskboard.service` — `127.0.0.1:8789`, Basic authentication,
  CSRF/Origin checks; the owner's local UI.
- SSH tunnel `bridge-pg-v2-ssh-shadow.service` — local `15433` → PostgreSQL.
- Managed Inbox receiver — `stage37b2c`, a plugin inside MultiTool, 30-second ticks.
- Agent CLI — `stage34d`: `peek` / `read` / `append` / `claim` / `ack`; the agent's only
  write path.
- Keeper `stage39`, watchdog `stage37a`, first-turn admission `registration` (30-second timer).

## Data flow

ChatGPT → MCP edge → PostgreSQL (`dialogs`, `messages`, `outbox`) → receiver claims a
message → MultiTool session → agent CLI appends a report → outbox to ChatGPT.

Owner browser → Taskboard → PostgreSQL. The owner can hand the turn back to the agent
without retracting a message that has already been delivered.

## Message and turn model

- `dialogs`: state, `next_actor`, version, `last_seq`.
- `messages`: `task` / `report` / `return` / `close`, with sender and recipient.
- `outbox`: one delivery row per message with status, attempts, lease and acknowledgement.
- An append requires the sender to equal `next_actor`; a successful append flips the turn
  and bumps the version. `expected_version` makes concurrent writes conflict-safe.
- Delivered messages are never retracted: `return_turn` cancels only a still-pending
  delivery, while `hand_back_turn` flips the turn to the agent and keeps everything
  already delivered.

## Permission model

- Local gate file `managed-writes-stage34d-armed` plus a short-lived permit
  `managed-mcp-write-permit35c.json`.
- The permit is either scoped to one dialogue or armed with `allow_create`
  (create dialogues and answer any non-canary dialogue).
- The watchdog quarantines invalid or expired permit/gate pairs. The keeper renews only
  existing capabilities and fails closed; it never recreates what was revoked.

## Deployment order

1. Schema: `sql/010_schema_full.sql` on an empty database — the complete skeleton
   (roles, tables, functions, owners and final grants, no data). `001_init.sql` →
   `003_hand_back.sql` are kept as the historical evolution, not as a restore path.
   Verify with `deploy/08_restore_check.sh` on the PostgreSQL host.
2. Python: `requirements.txt` (psycopg 3.x) in an isolated virtual environment.
3. Credentials, which are not part of this repository: database password, local MCP key,
   Ed25519 signing key.
4. Taskboard and tunnel units.
5. The pinned gateway release (`.pyz`) and the receiver plugin.
6. Keeper session, permit and the watchdog/admission timers.

Do not run the one-shot installers or recovery scripts on a live system without
revalidating pins, versions and authorization.

## Tests

`tests/` covers authentication, compatibility, core store behaviour, MCP transport and
the worker. They use fake credentials and never touch a live database.

## Not included

Credentials, DSNs, SSH keys, session and permit files, runtime logs, database dumps,
plugin builds and gateway release binaries.
