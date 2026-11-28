# P36.1 GitHub API budget and conditional cache

This release is based on deployed P36 `1d925cd98055cb9c41a4e7629ab0c24df3a0595b`.
It does not change who writes messages. Git branches remain authoritative,
MCP transmits metadata only, and the Taskboard remains read-only for GETs.

## Why

The real P36 rate-limit incident was `remaining=0/5000`: `dialog.load()`
repeatedly fetched repo/ref/commit/tree/blobs. The bridge's Git scanner and
the MT plugin polled independently, while Taskboard used the same backing
authority. One GUI operation could make multiple GitHub API calls.

## Changes

- `JsonHttp` in the real gateway and MT CLI now uses the **same** owner-only
  SQLite cache `/home/kali/agent-dialog-beta/state/github-api-cache.p361.sqlite`.
  Ownership/mode is verified; the DB must be 0600 and its parent 0700.
  Token-derived cache namespaces prevent mixing credentials; no token is stored.
- Mutable repo and dialog-ref GETs use `If-None-Match` for a cached ETag.
  Cached mutable data is **never** returned unless the authenticated server
  responds with HTTP 304. A 403/404/429 never falls back to stale data.
- Immutable commit, tree and blob GETs are cached by exact SHA and validated
  by the ordinary `GitHubDialog` snapshot checks. The caller still verifies
  the private repo and current branch before treating them as current.
- Headers `x-ratelimit-remaining`, `x-ratelimit-reset`, `Retry-After` are
  tracked centrally. A reserve of 150 API units halts further API attempts
  until reset; no implicit retry/spin. Public MCP reports `rate_limited`
  rather than incorrectly saying the authority has failed.
- Cache is capped at 500 responses / 32 MiB. No chat plaintext goes into
  Hermes MCP, but cache DB is private and MAY include message JSON, so it must
  not be published, uploaded or placed in Git.
- P36 Git scanner fallback interval is 60 s at end of a scan cycle (5 s
  while paging) and the MT JS fallback is 120 s. Metadata `dialog_notice`
  and `fs.watch` remain the fast path; lost notices are recovered by polling.
- Taskboard displays a read-only quota indicator via its existing
  authenticated `/api/dialogs` response. No extra GitHub request is made.

## Test gate before switching

1. In separate worktree: compile Python, run `pytest -q tests/`; check JS.
2. ETag 200 -> 304 must validate auth and reuse data, without new object GETs.
3. 403 on a mutable URL must not use cached results; 403/429 limit headers
   must trigger a bounded pause with no automatic retry.
4. Two different Python processes using the same token must reuse on-disk
   immutable cache; different token namespaces must not share private data.
5. Verify a deleted-ref/changed-head path remains fail-closed and no Git
   mutation is retried on uncertainty.
6. Release to a test worktree and compare actual GitHub rate-limit
   usage on repeated `peek` and P36 scanner ticks before production.

## Deployment

P36 code and installed JS plugin must be backed up and rolled back together.
The cache database is a NEW local artifact, never overwrite or chmod any
existing foreign file. Switch only on successful tests, then restart the
actual Hermes service and Taskboard (Taskboard must reload its own Python/JS
to show the quota meter). Reload the MT app for the new 120 s fallback interval.

## Limitations

This is not a GitHub webhook and does not eliminate all polling. An HTTPS
webhook needs secure publicly reachable ingress and a verified signature;
that should be a separate reviewed change, not a new open attack surface
introduced into P36.1. Quota is account-level, so unrelated API clients
outside our bridge may also consume it. The shared DB is an advisory local
budget for the configured credential, not a global GitHub billing API.
