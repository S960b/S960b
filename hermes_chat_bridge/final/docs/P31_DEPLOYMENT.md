# P31 release and rollback runbook — operator-executed, ChatGPT-authored

**Target repository:** `S960b/agent-dialogs`
**Baseline (installed P30):** `455d54a61e2e784d70170631508c044d15ee1a51`
**Development ref:** `fix/p31-native-mcp-contract`
**Status:** Draft, NOT a deployment authorization.
**Validated P31 SHA (MT, 2026-10-30):** `5d1e00263be2ea41d7341208db34ca4255f0e0a6` (1099 pytest passed; SDK probe passed).
**Real bridge SDK interpreter:** `/home/kali/.venvs/bridge-clean/bin/python` (confirmed via service ExecStart).
**Pytest interpreter:** `/tmp/gigatool/p26/.venv-test/bin/python`.

The working production checkout is reported as
`/home/kali/agent-dialog-beta/code`. Its tracked tree must be clean and
still point to the exact P30 baseline. Do not modify any OAuth credentials,
GitHub token, systemd unit, persistent registry, append grants or prior event
journal. Never paste service environment values, tokens or full log traces.

## Gate A — isolated verification (NO service changes)

1. Use `/tmp/p31-validation` detached worktree.
2. Fetch the exact P31 commit from GitHub; record full 40-char SHA.
3. Run `python -m pytest -q tests/` with the known test interpreter.
4. Run `python scripts/p31_sdk_probe.py` with the **actual bridge interpreter**
   (`/home/kali/.venvs/bridge-clean/bin/python`), with
   `PYTHONPATH=/tmp/p31-validation/src`. This uses the installed SDK but registers
   tools ONLY in a temporary in-memory FastMCP object. No HTTP, socket,
   credential or Git operation.
5. Report green tests, schema probe status, exact SHA and clean worktree.

The old `.venv-beta` contains no `mcp` package. A probe run under that
interpreter will return `sdk_registration_unconfirmed` but does **not**
indicate incompatibility of the production bridge. Do not install packages
or mutate either virtual environment merely to make the wrong probe pass.

The fake-MCP tests alone **do not establish compatibility** with the installed
MCP SDK. The probe must confirm all six tools, required `task_id`,
`readOnlyHint`, `destructiveHint`, `idempotentHint`, and OAuth metadata.

## Gate B — pre-deployment environment inspection (read-only)

These are proposed operator checks, NOT a request to deploy:

```sh
CODE=/home/kali/agent-dialog-beta/code
test "$(git -C "$CODE" rev-parse HEAD)" = 455d54a61e2e784d70170631508c044d15ee1a51
test -z "$(git -C "$CODE" status --porcelain)"
systemctl --user is-active hermes-bridge.service
systemctl --user show hermes-bridge.service \\
  --property=ActiveState --property=FragmentPath \\
  --property=WorkingDirectory --property=MainPID --no-pager
```

Confirmed by MT on 2026-10-30: `hermes-bridge.service` running as user unit,
WorkingDirectory `/home/kali/S960b/research/hermes_chat_bridge`; its
`bridge_server.py` inserts `/home/kali/agent-dialog-beta/code/src` in `sys.path`
at startup (beta_wakeup, gateway, notify). No editable install or PYTHONPATH
binding is needed. The proven SDK interpreter is
`/home/kali/.venvs/bridge-clean/bin/python`. Do not output `Environment`, `ExecStart`, credentials or
unredacted journal content. Inspect service-specific shutdown/dependencies
before executing any switching steps. The MT inbox/session should survive or
be explicitly reconnected afterwards.

## Exact P31 candidate and smoke ordering (operator checklist)

For the **first live deployment**, use the proven code SHA
`5d1e00263be2ea41d7341208db34ca4255f0e0a6`. Later commits are
documentation-only and not necessary for service operation. All commands below
must be run as the **same non-root systemd user** who owns the bridge.
Do not use `sudo`, `git reset --hard`, `git clean`, `git push`, or
`systemctl daemon-reload` in this upgrade.

Before any write, record full HEAD and clean status from the production
checkout, and verify that P30 is an ancestor of the pinned P31 commit.
Fetch the P31 branch but do not switch a running production checkout without
stopping the bridge first.

```sh
set -euo pipefail
CODE=/home/kali/agent-dialog-beta/code
P30=455d54a61e2e784d70170631508c044d15ee1a51
P31=5d1e00263be2ea41d7341208db34ca4255f0e0a6
test "$(git -C "$CODE" rev-parse HEAD)" = "$P30"
test -z "$(git -C "$CODE" status --porcelain)"
git -C "$CODE" fetch origin refs/heads/fix/p31-native-mcp-contract
git -C "$CODE" cat-file -e "$P31^{commit}"
git -C "$CODE" merge-base --is-ancestor "$P30" "$P31"
git -C "$CODE" diff --check "$P30" "$P31"
systemctl --user is-active --quiet hermes-bridge.service
```

Only after this passes, stop the **bridge only**, verify stopped, switch
the clean checkout and start it. If ANY command fails, stop and use the
manual rollback below; never auto-repeat a failed publication.

```sh
set -euo pipefail
CODE=/home/kali/agent-dialog-beta/code
P31=5d1e00263be2ea41d7341208db34ca4255f0e0a6
systemctl --user stop hermes-bridge.service
if systemctl --user is-active --quiet hermes-bridge.service; then
    echo "ABORT: bridge still active" >&2
    exit 1
fi
test -z "$(git -C "$CODE" status --porcelain)"
git -C "$CODE" switch --detach "$P31"
systemctl --user start hermes-bridge.service
systemctl --user is-active --quiet hermes-bridge.service
test "$(git -C "$CODE" rev-parse HEAD)" = "$P31"
test -z "$(git -C "$CODE" status --porcelain)"
test -f /home/kali/agent-dialog-beta/state/authority-events.p31.sqlite
test "$(stat -c %a /home/kali/agent-dialog-beta/state/authority-events.p31.sqlite)" = 600
echo "P31_STARTED"
```

Read-only check from the MT-owned Unix peer (this does NOT bind the ChatGPT
OAuth identity for new P31 event subscriptions):

```sh
cd /home/kali/agent-dialog-beta/code
printf '%s\n' '{"op":"health","task_id":"task_7d2c928d6e75e7173c95d1e2e57a52ea"}' |
  env -u APPDIR -u APPIMAGE PYTHONPATH=src .venv-beta/bin/python -m git_dialog.unix_authority cli
```

Check head/waiting_for, no message bodies, and do not infer delivery merely
from successful CLI health. The authenticated ChatGPT client must refresh its
MCP tool catalog and invoke `dialog_health(task_id=...)` or `dialog_view`
after the restart so `request_notification_owner` binds the new journal.
**Do not trigger the MT smoke append before this authenticated call succeeds.**

Once ChatGPT confirms its MCP call, MT can perform a single new-dialog smoke
with a stable request ID. This test creates a Git branch (intended live write).
Capture the returned `task_id` and `head` exactly, then use them ONCE in
an MT `append` request, with `kind="report"` and a unique stable message_id.
After the result, record `commit`, `created`, and check whether the native
subscriber received exactly one event for that task/commit. If response is
uncertain, do **not** choose another ID or retry automatically; use
`creation_status` / `view` to reconcile the exact original attempt.

```sh
cd /home/kali/agent-dialog-beta/code
printf '%s\n' '{"op":"create_dialog","request_id":"p31_smoke_20261030_v1","title":"P31 notification smoke"}' |
  env -u APPDIR -u APPIMAGE PYTHONPATH=src .venv-beta/bin/python -m git_dialog.unix_authority cli
# STOP HERE, copy confirmed task_id and head into the next exact append.
# A sample append schema (replace <TASK_ID> and <HEAD> from the returned receipt):
# {"op":"append","task_id":"<TASK_ID>","head":"<HEAD>",
#  "message_id":"p31_smoke_mt_report_v1","kind":"report","body":"P31_PUSH_SMOKE_V1"}
```

If the server is running but the ChatGPT plugin does not yet expose
`dialog_health`, first refresh/reconnect its MCP connection. Do not
misdiagnose stale client metadata as a server failure.

## Manual rollback (only if installation/live checks fail)

```sh
set -euo pipefail
CODE=/home/kali/agent-dialog-beta/code
P30=455d54a61e2e784d70170631508c044d15ee1a51
P31=5d1e00263be2ea41d7341208db34ca4255f0e0a6
LIVE="$(git -C "$CODE" rev-parse HEAD)"
test "$LIVE" = "$P30" || test "$LIVE" = "$P31"
test -z "$(git -C "$CODE" status --porcelain)"
systemctl --user stop hermes-bridge.service
if systemctl --user is-active --quiet hermes-bridge.service; then
    echo "ABORT: bridge still active" >&2
    exit 1
fi
git -C "$CODE" switch --detach "$P30"
systemctl --user start hermes-bridge.service
systemctl --user is-active --quiet hermes-bridge.service
test "$(git -C "$CODE" rev-parse HEAD)" = "$P30"
echo "P30_RESTORED"
```

Keep the P31 event journal file for later diagnosis, **do not delete it**.
A rollback of Python source is not a rollback of already-published Git
messages; any smoke branch and appends remain durable.

## Gate C — planned maintenance deployment (NOT yet authorized)

Only after gates A and B are green, record the tested `P31_SHA` as the full
commit SHA and arrange a controlled service interruption. The high-level,
reversible sequence is:

1. Confirm the live checkout still equals P30 and `git status` is empty.
   Confirm the tested P31 commit is present as a local Git object.
2. Stop **only** `hermes-bridge.service` using the known user systemd manager.
   Verify it stopped. Do not stop Tailscale or alter credentials.
3. Switch the clean production checkout to `P31_SHA` in detached mode using
   Git (no reset/force; no automatic merge). This changes tracked source only.
4. Start the bridge. Verify `/ping`, native MCP `tools/list` and a read-only
   `dialog_health(task_id=...)` on a known existing task.
5. Validate notifications from a **new disposable dialogue** using stable
   message IDs. Check recipient, event dedupe, session affinity and report
   journal state. Never fabricate an MT turn or retry an uncertain publication.
6. If any gate fails, stop bridge, `git switch --detach` back to the exact
   P30 baseline, start service, and verify the old /ping and MCP tools again.
   Preserve P31 SQLite event journal for diagnosis; never erase it blindly.

An interrupted switch, failed shutdown or dirty tree MUST stop the procedure
for manual inspection; no auto-retry, force reset, chmod/chown, branch deletion
or cleanup of live databases is permitted.

## P31 runtime health contract

`dialog_health(task_id)` is a read-only authenticated MCP operation. Result
contains only `task_id`, Git `head`, `waiting_for`, `status`, `stalled`,
`seconds_since_message`, `last_message_at`, and `last_error_code`.
`last_error_code` is a sanitized, bounded *process-local* memory value:
null means no recorded error in this authority lifetime, **not proof that
the service has never failed**. It is lost at restart. A stalled marker is a
10-minute inactivity indication, not permission to steal a turn.

`dialog_view` remains limited to at most 100 messages and approximately
256 KiB in the history page; follow `next_offset`. GET-like operations may
still load and validate bounded persisted history internally but never mutate
remote Git refs.

## Abort criteria

- Any existing P30 test or SHA mismatch.
- Real FastMCP schema mismatch/unknown annotations or missing required task_id.
- Unexpected peer/owner authorization behavior.
- New-dialog event delivery missing, duplicate, misrouted, or mixed with pilot.
- Existing P26 event journal or P30 append/grant database changes unexpectedly.
- Failed service stop/start or inability to roll back to the exact P30 commit.
