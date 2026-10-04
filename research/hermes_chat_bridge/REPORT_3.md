# REPORT_3 — P0/P1 review of adfe711, final acceptance

## Scope and deployment status

Baseline: `adfe711c91cd35a1c1d5c4ca4ce572a52f8f19ef`. The supplied review,
original reproducing tests and reviewer results are preserved unchanged under
`reports/review_adfe711_materials/`. The development-stage report (70 passed,
3 xfailed) is historical: `reports/implementation-review3-stage.md`.
The final acceptance below supersedes that intermediate count.

This change does not rewrite the bridge. It implements the supplied P0/P1 plan
and chooses a fresh, explicit synthetic pilot database instead of legacy migration.
The existing live server was NOT restarted or replaced. The public tunnel remains
off. During this revision the real old database was not opened, read, migrated,
backed up or changed; this statement does not erase the early CLI SELECT incident
already documented in REPORT_2. Only temporary synthetic databases were used here.

`chatgpt_connected`, `subscription_verified`, `same_chat_verified`, and
`roundtrip_verified` remain **unverified**. No Work-chat response or actual
`BRIDGE_OK:<job_id>` has been produced. HTTP 2xx is not a chat response.

## Fixes

### P0: revocation API and SDK contract

- Enabled `RevocationOptions(enabled=True)` in actual SDK AuthSettings. `/revoke`
  is routed and advertised in authorization-server metadata.
- `revoke_token` accepts SDK `AccessToken | RefreshToken`, using `.token`, not
  passing a Pydantic object into a SQLite binding.
- Revocation deletes access/refresh tokens for the same client/subject. This
  owner-only bridge conservatively cancels all subscriptions for that owner and
  terminates their pending deliveries with `access_revoked`.
- SDK/ASGI checks cover access and refresh, repeated HTTP revocation, wrong-client
  no-op, metadata, revoked-token MCP rejection and outbox cancellation. They are
  not a test of ChatGPT's UI disconnect.

### P0: pilot database admission

- No implicit production DB fallback: service requires explicit absolute
  `BRIDGE_DB_PATH`. Noncanonical paths, `..`, symlinks and nonregular files fail.
- A fresh Store has `PRAGMA user_version=3`. Service startup inspects existing
  databases read-only and refuses unversioned/unsupported DBs BEFORE schema writes.
  Reopening a previously created pilot DB is supported.
- Lifetime kernel `flock` on `<DB>.pilot.lock` prevents a second service process
  for the same DB, independent of bind port. Never delete/replace a held lock file.
- Direct `Store`/`BridgeApp` are internal fixture APIs, not a supported service
  entry point for upgrading legacy data. Legacy migration is NOT implemented.
- Before an authorized real pilot: preserve the old DB/process state and select a
  new DB outside the Git checkout; do not copy old subscriptions into it. A future
  old-data migration needs its own approved SQLite backup and verified migration.

### P1: cancellation, terminal state, wire errors and transport

- Conditional activation uses generation/pending_generation. Unsubscribe/revoke
  invalidate pending operations. Late successful challenge cannot resurrect
  cancellation or overwrite a newer secret. Failed rotation preserves working
  parameters. Principal/token is checked again after the challenge await.
- Terminal delivery transitions use `UPDATE ... WHERE done=0`; stale failure
  cannot reopen delivered/revoked rows. Overlapping delivery loops return promptly
  through a nonblocking lock rather than holding cancellation behind a network wait.
- Callback and invalid-parameter failures raise SDK `MCPError` (-32015 / -32602).
  The wire reply has a TOP-LEVEL JSON-RPC `error` and no successful `result`.
- Pinned HTTPS explicitly completes TCP/TLS, then checks active/expiry/generation
  immediately before writing the request; it closes the connection on refusal.
  Existing public-IP pinning, original TLS hostname/SNI, no redirects/environment
  proxies, bounded worker pool and exact Standard Webhooks bytes are preserved.

## Reproducible evidence

Python 3.13, pinned existing clean environment `/tmp/bridge-clean-venv`;
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. The final complete suite ran once against the
Git publication tree after adding the parent's three independently RED-confirmed
acceptance tests. No production code changed after that run.

| Check | Actual result | Evidence |
|---|---|---|
| Unmodified supplied tests on baseline adfe711 | 13 failed, 4 passed | reports/review3-parent-baseline.txt |
| Independent TLS cancellation RED | 1 failed: request bytes written after cancel | reports/bridge-review3-transport-red.txt |
| Independent cross-connection terminal CAS RED | 2 failed: terminal rows reopened | reports/bridge-review3-cas-red.txt |
| Startup RED on baseline | implicit DB startup incorrectly accepted | reports/bridge-review3-startup-red.txt |
| Final full pytest / ASGI | **73 passed, 3 strict xfailed**, 2.14 s, exit 0 | reports/review3-final.txt and .xml |
| Real isolated loopback HTTP smoke | **12 passed, 0 failed**, exit 0 | reports/review3-isolated-http.txt |
| Real child-process startup admission | **5 checks passed**, exit 0 | reports/review3-pilot-startup.txt |
| pip check | No broken requirements found | reports/review3-pip-check.txt |

The original 45 cases remain in the suite. Direct-call tests were adapted to the
SDK error/token contracts without removing their state assertions. The supplied
stale-worker test now separately records terminal success while requiring an
overlapping delivery loop to return promptly. Original supplied tests remain
available for comparison in the review-materials directory.

The three independent acceptance checks prove: TLS/setup was actually reached
(not vacuous early rejection), no HTTP request bytes were written after observed
cancel, the socket closed, and a second SQLite connection's stale failure cannot
change ANY terminal-row fields, including reason/status/attempt metadata.

The five actual-process checks prove: missing explicit DB is rejected, legacy
DB is rejected with unchanged bytes, a fresh explicit DB is used, a second process
on a different port is rejected, and the first pilot DB reopens after lock release.
All children are stopped and temporary HOME/DBs removed by the runner. No external
callback requests occur in these checks.

The 12 HTTP checks cover OAuth/login, protected MCP, discovery/events and callback
error serialization. Actual revocation is exercised separately through the SDK's
ASGI routes in the full suite; do not present the 12 smoke checks as a UI revoke test.

## Build identity

Version is `0.2.0`, also present in SDK server metadata. `/ping` captures process
version/revision once at app creation, not by reading the latest report. A clean
Git checkout exposes its actual HEAD; outside Git or when dirty it reports
`unknown`. Precommit smoke logs therefore correctly show `unknown`. The additional
startup runner can require the precise clean-checkout revision:

`python scripts/verify_pilot_startup.py /absolute/project/path <expected-full-commit>`

That verification starts only temporary loopback processes; it does not upgrade
or prove the version of the old live process.

## Deliberately open limitations

Exactly three strict known-limitation xfails remain visible:

1. `test_legacy_active_flag_is_not_proof_of_callback_verification`: no safe legacy
   migration is claimed; public service admission rejects that DB instead.
2. `test_stale_loaded_oauth_grant_cannot_be_exchanged_twice[code]`.
3. `test_stale_loaded_oauth_grant_cannot_be_exchanged_twice[refresh]`.

Sequential HTTP replay is rejected, but stale-loaded grant consume is not atomic
across multiple processes/connections. Pilot is restricted to one service process
and one delivery loop. Multi-process OAuth/outbox leases are deferred, not fixed.
Already-written bytes cannot be recalled; exactly-once delivery is not claimed.
CLI job/outbox use separate commits; no transactional crash-recovery guarantee.
No overlapping old/new signing keys and no total hard DNS+TCP+TLS deadline.
The bridge is owner-only, NOT multi-user isolated.

## Remaining real pilot

Only after final-code approval: preserve old runtime/DB state, start exactly one
current process with a new private test DB, verify actual build, enable the approved
tunnel, connect the intended Work chat, and exchange one synthetic
`BRIDGE_OK:<job_id>`. Verify duplicate event handling and UI cancellation separately.
No real user/financial data should enter that first pilot. This report is a code
acceptance artifact, not proof of public deployment or ChatGPT integration.
