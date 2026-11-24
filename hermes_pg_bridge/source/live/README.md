# Bridge v2 / PostgreSQL (PHASE A1 — NOT YET DEPLOYABLE AS A COMPLETE MCP BRIDGE)

**Author:** ChatGPT. **Executor:** MT/Hermes agent (commands only). **Owner:** user.

This archive contains the first self-contained backend slice: a fresh PostgreSQL schema, short-lived psycopg client, tests and conservative setup scripts. It does **not** modify the existing Git bridge or run any production service. Source contract inventory is needed before porting MCP tools, worker, UI, auth and transport.

See `TECHNICAL_PLAN.md` for the full implementation schedule and roles.

## Safe deployment order (only after reviewed and approved)

On the **PostgreSQL host** (NOT automatically the Kali host):

```bash
bash deploy/00_preflight.sh              # read-only
sudo bash deploy/01_db_install.sh       # manual typed confirmation; creates schema
sudo bash deploy/02_prepare_python.sh   # isolated venv, system user; no daemon
sudo bash deploy/03_setup_pgpass.sh     # root-only password -> local service .pgpass
sudo bash deploy/04_smoke.sh            # adds a small test dialogue
```

Preflight's `runuser` requires root; run preflight with `sudo` if needed. The database must be a fresh, dedicated `bridge` database with `bridge_app` login and secret stored at `/root/bridge-db-password.txt` (from the approved prep note). **Do not expose port 5432.** No passwords should appear in logs or message bodies.

All running old services stay untouched. Do not run the scripts on the wrong host or before ChatGPT reviews the agent's live inventory.

## Failure handling

- Don't rewrite the SQL by hand, don't delete tables.
- Return exit code + nonsecret error and postgres version to ChatGPT.
- A failed SQL transaction rolls back the schema, but if the overall setup is partially complete, stop and request new code from ChatGPT.
- Smoke test creates records and requires a disposable / fresh database.

## Tests

`PYTHONPATH=. BRIDGE_TEST_DSN='...' python -m pytest -q tests` on a **disposable** PostgreSQL database initialized with `001_init.sql`. Do not put secrets in shell history; use PGPASSFILE/libpq environment config instead. Without a DB, Python `compileall` is a safe syntax-only check; it is not a database integration test.

## Инвентаризация 2026-11-22 (важно для развёртывания)

- Текущий исходник старого моста: `/home/kali/agent-dialog-beta/code` на Kali. Не менять его рабочее дерево.
- Старые порты Kali: MCP `8765`, Taskboard `8787`. Порт `8788` занят NEWS Monitor; для панели v2 выбран `8789`, после проверки доступности.
- PostgreSQL находится на **удалённом отдельном сервере**, слушает `127.0.0.1:5432`. Нельзя запускать локальный Kali worker с прямым `Store()` к удалённому PostgreSQL. Нужен аутентифицированный HTTPS API доставки на DB-хосте и локальный durable inbox адаптер на Kali.
- SQL отклоняет привилегированную роль `bridge_app`, переносит владельца базы на `bridge_owner NOLOGIN` перед созданием таблиц и выдаёт `bridge_app` лишь CONNECT/EXECUTE.
- Никаких установок на DB-хост до подтверждения доступа и отдельной проверки скрипта.
- Текущий пакет `bridge_pg.worker` — **ядро алгоритма доставки**, не MT-интеграция и не служба для Kali.

## Phase A2 — actual legacy compatibility adapter (2026-11-22)

The source export was confirmed at `S960b/agent-dialogs`, branch
`migration/pg-v2-source`, commit `993107c66e51b160cc3aa06c2b9ee39759b2af8b`.
The following **new files were authored by ChatGPT**, not the execution agent:

- `sql/002_compat.sql`: creation lookup, metadata, soft deletion, individual
  receipt retrieval and an atomic per-message MT delivery lease.
- `bridge_pg/compat.py`: v2 operations mapped into the current 8-tool dialog
  contract and the Taskboard API. `head` is a **synthetic 40-hex version token**,
  never a Git SHA, and must not be presented as a Git reference.
- `bridge_pg/cli.py`: separate MT CLI supporting list/peek/read/append/status,
  including an explicit version check and `--body-file` to keep message content
  out of process arguments.
- `bridge_pg/taskboard_v2.py`: localhost-only, Basic-auth/CSRF protected
  Taskboard API at **8789**. Deletion means *tombstoning*, not erasure.
- `tools/port_taskboard_assets.py`: copies the **actual** existing UI's
  HTML/CSS/JS into isolated v2 assets, changing Git-specific labels.
- `tools/port_mt_plugin.py`: transforms a copy of the existing MT plugin into
  the isolated `dist/mt-pg-inbox.mjs` with separate state directory, PG CLI,
  fail-closed outbox claim and post-prompt acknowledgement.
- `deploy/05_kali_prepare.sh` and `deploy/06_check_assets.sh`: offline checks
  and isolated preparation on Kali only. They never start services.

### Correct two-machine topology

Use **SSH local port forwarding**, not an unreviewed public DB API:

```text
ChatGPT -> MCP OAuth :8766 (Kali) -> PG Store on 127.0.0.1:15432 (Kali)
                                       | encrypted SSH localhost forward
                                       v
                     127.0.0.1:5432 PostgreSQL (remote DB server)

MultiTool plugin (Kali) -> identical local PG Store -> same SSH tunnel
Taskboard v2 :8789 (Kali) -> identical local PG Store -> same SSH tunnel
```

Neither PostgreSQL nor its password should be exposed over the public
internet. SSH server host keys must be verified and pinned; port forwarding
must bind explicitly to `127.0.0.1`, not `0.0.0.0`. `bridge_app` credentials
must stay in local protected files, never in a command, URI or public message.

### Unfinished before production rollout

- PostgreSQL `001` and `002` need a disposable integration test on DB host.
- The MCP 8766 OAuth bootstrap is awaiting export/review of the real current
  `start_bridge.py` (not available in the committed source tree).
- The actual Taskboard assets/plugin transforms require a dry-run on Kali and
  must be checked against the live SHA before enabling.
- Ownership of the cross-host SSH route, `.pgpass` provisioning and service
  isolation must be confirmed without publishing credentials.
- DO NOT start live v2 services or load `mt-pg-inbox.mjs` until the above gates.

### Status of phase A2 package

Author-supplied code is **not yet a production release**. `mcp_v2.register_mcp`
contains nine OAuth-guarded tools, including the essential new `dialog_view`
for reading actual MT report bodies directly from PostgreSQL. Hosting/bootstrap
still requires the inspected existing OAuth startup wiring. The code currently
uses the actual old UI and MT plugin as source inputs to separate *copy-once*
transform scripts; they never modify the originals.

Taskboard authentication must be initialized interactively as the Kali service
account with `python -m bridge_pg.taskboard_v2 init-auth` before first start.
Do not paste the chosen password into ChatGPT/Git or environment dumps.

The v2 local database config is a mode-0600 file at
`~/.config/bridge-pg-v2/conninfo` and the matching libpq passfile lives next
to it. The new CLI uses port 15432 by default rather than ever connecting to
remote PostgreSQL directly. See `deploy/07_config_example.txt`.
