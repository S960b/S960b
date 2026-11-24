# Hermes PG v2 — Stage 34E-B: one-pilot active-capable managed receiver

**PREPARE ONLY; no activation or database mutation.**

The Stage34E-A shadow receiver is correct but has `dryRun=true` permanently.
Stage34E-B compiles a separate candidate that *could* execute SDK if and only if
a separate future configuration registration explicitly supplies a single
non-canary `task_id`, a managed dialog is enabled server-side, and the
independent Stage34D CLI write-allow file exists. **None of these conditions is
established by this package.**

## Preconditions

- Stage34E-A output:
  `/home/kali/hermes-pg-stage34e-managed-runtime/mt-pg-inbox-managed-stage34e-shadow.mjs`
  SHA256 `1580178d508174af1edff784b87acd11d339b1d306a057f6112786ffb36122e8`.
- Live seven-plugin MultiTool config SHA256 `e5320f6e971073d5d2c43a64ce85d165cb49dc9a94e6cf7806009ebc2b59ded1`;
  Stage33I canary filter remains installed and fixed.
- Stage34D CLI SHA256 `bf8cad69a02e71a70ec3377504c24a3fc2cb71a38f2c8c4a025abc587b774485`.
- PostgreSQL: 4 tables, 42 functions, empty managed registry, canary seq5/6 delivered.
- Stage34D managed write gate ABSENT.
- Role-bound SSH forward on 15433, Secure MCP Tunnel unchanged.
- Agent acts under OS user `kali`, not root.

## What is produced

An unregistered `mt-pg-inbox-managed-stage34eb-pilot-unregistered.mjs`
in `/home/kali/hermes-pg-stage34eb-pilot-candidate`.

Changes from the byte-pinned Stage34E-A source:

1. `dryRun=true` becomes `dryRun=false` in this **new, unregistered file only**.
2. A private one-pilot selector reads `options.task_ids`. Exactly one valid
   `task_[32 hex]` other than the pre-existing canary must be provided.
   Missing, malformed, multiple or canary IDs result in **NO NEW WORK**.
3. In `runTick()`, a fail-closed guard filters all `ag_dispatch_list()` entries
   by this explicit one-pilot selector. The server-side managed allowlist is
   still required.
4. The existing unconditional durable `inflight` reconciliation runs BEFORE
   discovery and continues regardless of whether pilot configuration is
   later removed or a dialog stops being eligible for new dispatch.
5. All actor CLI, SQL ACK, report verification and state paths are preserved.

This phase neither creates a managed dialog nor registers a plugin, creates a
write gate, sends a task, calls SDK, changes MultiTool config, modifies services,
or attaches a new MCP to the Secure Tunnel.

## Commands

Download the ZIP to
`/home/kali/Downloads/hermes-pg-stage34eb-pilot-candidate.zip`, verify outer
SHA256 provided by ChatGPT, unzip to `/home/kali` and run from that directory:

```sh
sha256sum -c SHA256SUMS
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python -m unittest -v test_stage34eb
/home/kali/.hermes/node/bin/node --test test_gate34eb.cjs
/home/kali/bridge-pg-v2/.venv/bin/python -m py_compile pilot34eb.py test_stage34eb.py
for f in *.sh; do bash -n "$f"; done
bash 83_readonly_preflight.sh
bash 84_prepare_once.sh
bash 85_verify_only.sh
```

**One-shot** `84_prepare_once.sh` must not be rerun after any partial failure.
Use ONLY `85_verify_only.sh` for subsequent checks. Any mismatch with the
current live source/config or role checks must STOP without attempting edits.

Expected:
- `STAGE34EB_STAGE34E_REAL_DB_AND_SEVEN_PLUGIN_PREFLIGHT_OK`
- `STAGE34EB_READ_ONLY_PREPARE_READY`
- `STAGE34EB_LIVE_CAPABLE_UNREGISTERED_CANDIDATE_CREATED`
- `STAGE34EB_ACTIVE_CAPABLE_SOURCE_SYNTAX_OK`
- `STAGE34EB_EXACT_ONE_MANAGED_ID_FILTERED`
- `STAGE34EB_CANARY_EXCLUDED_AND_EMPTY_FILTER_FAIL_CLOSED`
- `STAGE34EB_ALL_INFLIGHT_RECONCILE_PRESERVED`
- `STAGE34EB_LIVE_CONFIG_AND_WRITE_GATE_UNCHANGED`
- `STAGE34EB_PREPARE_COMPLETE_NO_REGISTRATION_OR_WRITE_ARM`
- `STAGE34EB_VERIFY_ONLY_NO_DB_NO_SDK_NO_TUNNEL_CUTOVER`

## Next stage (NOT performed here)

Create exactly one disabled managed pilot dialog under `bridge_chatgpt` using
a fixed, bounded test interface, inspect its ID and policy. Then prepare an
exclusive/guarded registration of the managed plugin with precisely that ID,
without removing the Stage33I canary plugin. Only after independent permission
checks and explicit owner approval may the test dialog be enabled, the
Stage34D write gate be armed, and one harmless SDK test task sent.

The final unrestricted MCP requires user authentication, and the final production
system should separate the ChatGPT and MultiTool UNIX credential access.
