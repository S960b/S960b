# P36 deployment gate (Git-first, metadata-only Hermes)

**Current status (2026-11-08): P36.2 installed and smoke-verified**, checkout `b63116d35dc200db28b95393946f990e807c29dd`; see [P36.2 release acceptance](P362_RELEASE_CLOSED_20261108.md). The P35 / 493707e7 candidate below is an **historical runbook**, not a command to replace the current deployed release. Current rollback baseline is the privately preserved **P36.1** plugin/checkout, not P35.

## Scope
This is a controlled replacement of P35 at the owner's direction. No old
message migration is required, but do not delete old Git branches or local DBs.
No assets, trading keys, or Binance processes are involved.

Release candidate: `493707e7a5da1160bf11ceaa4ce3064a644dd46b`
Base P35: `edca980ec8dddff00d625fff444a4d9a9ffd61c6`
Source branch: `feature/p36-direct-git-notify`

## Before changing live files
1. In a **separate** worktree, run full Python tests and Node smoke on the
   exact release candidate, record pass count. Ensure tests do not touch live state.
2. Inspect the actual `hermes-bridge` and Taskboard unit ExecStart, Python
   environment and working directory; do not assume service names/permissions.
3. Ensure live checkout clean, exactly P35 HEAD. Record service status, file
   SHA256 and private backup of `mt-git-inbox.mjs`; do not print credentials.
4. Check `authority-events.p36.sqlite`: new zero-byte owned regular file is
   expected and should be initialized on first start; never delete or chmod
   a different/foreign file.
5. Verify the new Git-first plugin has no embedded credentials. Check that
   its `CODE` and `PYTHON` constants point to the actual deployed checkout.

## Deployment sequence
1. Preserve rollback reference to P35 and the original plugin. Fetch the
   exact P36 commit and verify ancestry of P35, clean tree and SHA.
2. Stop the **actual** bridge service gracefully before switching live code.
   Do not rewrite, force-push, or delete remote Git dialogue refs.
3. Switch live checkout to the exact P36 commit. Restart bridge service.
4. Verify health, Unix socket, PID, no startup exception and metadata-only
   MCP tool registration. P36 public MCP must contain `dialog_index` and
   `dialog_notice`, and must not expose `dialog_view` or `dialog_append`.
   Existing local Unix view/append are retained for Taskboard/P35 plugin
   migration and must not be exposed through public MCP.
5. Install `deploy/mt-git-inbox-p36.mjs` as the local plugin only after
   bridge checks succeed. A running MultiTool/GigaTool application may have
   the old JS module cached: a controlled app/plugin reload is required to
   activate the new version. Do not assume copying the file hot-reloads it.
6. After reload, run a **new isolated task** both ways: ChatGPT writes a
   Git commit directly, then `dialog_notice` carries IDs/SHA only and
   local wake file triggers plugin; MT reads Git directly and replies via
   `direct_git append`, then local Unix `notice` triggers metadata-only
   Git event delivery back to ChatGPT.
7. Validate one-and-only-one delivery by `message_id` and `seq`, not just
   a successful HTTP response. Test a missed notice is recovered by polling.
8. Compare error-free service logs and state counts; remove stale test
   subscriptions only under a separate reviewed cleanup, not now.

## Rollback
If the bridge fails to start, or the new plugin cannot read Git, restore the
recorded P35 checkout and exact P35 plugin file, restart only the services
that were changed, and verify P35 health. Preserve P36 Git branches and
journal as forensic evidence, without trying to import it into P35.

## Security invariants
- No secrets/body text in Hermes MCP notifications or logs.
- Model-controlled content is inert data, not executable command.
- Git writes use immutable messages plus state in one tree/commit, nonforce
  ref update against the verified parent, and GET-only reconciliation on
  uncertainty.
- Never retry an uncertain append with a different message ID.
- If service state, user permissions, app configuration, or cleanup differs
  from this runbook, stop at the unexpected condition and report it.
