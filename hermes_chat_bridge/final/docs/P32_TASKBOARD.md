# P32 Git-dialog taskboard — new bridge, not legacy pilot.db

**Статус на 4 ноября 2026: УСТАНОВЛЕНО, версия P35.** Работающий Taskboard на `127.0.0.1:8787` дополнен ровно одной колонкой **«Последнее действие»**. Она различает Git-запись, подтверждённую отправку в MT-сессию и фактический ответ MT; `inflight` не выдаётся за успешную доставку. Состояние MT читается локально и отдаётся только в сокращённом виде (`source`, `seen_seq`, `inflight_seq`), без session ID. P35 код сохранён коммитом `edca980ec8dddff00d625fff444a4d9a9ffd61c6`; этапы установки ниже — **исторические, повторно запускать их на живой службе не нужно**.

Source: private S960b/agent-dialogs, branch feature/p32-git-taskboard.
Browser: http://127.0.0.1:8787 (local Kali only).
New service: git-dialog-taskboard.service. Do not silently restart old
hermes-taskboard.service or read old pilot.db.

Rows show Git dialogues (title, full task_id, lifecycle, latest message_id).
Each dialogue's History shows newest message first, with message IDs and full
text. Individual task_id and message_id have copy buttons.

## Authentication and trust boundary

Initialize the *new* owner authentication once using a local hidden password
prompt (not the old bridge password). The private verifier is stored in
/home/kali/agent-dialog-beta/taskboard-auth/panel.sqlite, mode 0600,
inside an owner-only directory, using the existing scrypt AuthAuthority.

The UI connects ONLY to existing MT Unix authority-mt.sock. It never reads
GitHub PAT, pilot.db or raw lifecycle SQLite. It verifies Basic authorization,
loopback Host, Origin and X-CSRF-Token on writes. Do not forward port 8787
through Funnel or a public proxy. The separate bridge on 8765 is unchanged.

## Git branch deletion

The Delete task button actually deletes the exact dialogue's Git ref,
but only after a typed full task_id confirmation and two-phase authority
procedure:
1. Read the current HEAD, and verify again in the authority process.
2. Persist local closing state and issue an opaque 300-second nonce.
3. Verify nonce, latest HEAD and expiry; persist tombstone BEFORE Git DELETE.
4. Delete the exact refs/heads/dialogs/<task_id> branch through approved
   credential-bound GitHub transport; verify remote absence.

If the Git DELETE result is uncertain, leave task tombstoned and STOP.
No automatic retries. GitHub ref DELETE is not server-side conditional;
external writers must be excluded during operator deletion. Deleting a
ref does not wipe cached Git objects, forks, backups or clones.

## Gate A: isolated test before production installation

- Fetch feature/p32-git-taskboard into a new detached test worktree.
- Full pytest on the known verified test Python environment:
  PYTHONPATH=src /tmp/gigatool/p26/.venv-test/bin/python -m pytest -q tests/
- Confirm all tests green; no edits, no service restart, no Git mutation.
- Confirm SHA, dirty status, and exact first traceback if failing.

## Gate B: исторический сценарий установки (уже выполнен)

P31 live SHA was 5d1e00263be2ea41d7341208db34ca4255f0e0a6.
Preserve current HEAD and clean working tree for exact rollback. Verify
tested P32 SHA is a descendant of P31. Shut down only hermes-bridge.service
when switching source to P32, then restart and check /ping, Unix health.

Initialize owner auth from a tty, without putting password in logs:
PYTHONPATH=/home/kali/agent-dialog-beta/code/src /home/kali/.venvs/bridge-clean/bin/python -m git_dialog.taskboard init

Do not remove existing old unit/backups. Stop+disable legacy user
hermes-taskboard.service to free port 8787. Install the new
deploy/git-dialog-taskboard.service into
~/.config/systemd/user/git-dialog-taskboard.service, daemon-reload,
enable --now git-dialog-taskboard.service.
Expected HTTP 401 without Basic, and authenticated rows in browser.
Verify binding ONLY on 127.0.0.1:8787. Do not test actual deletion on any
production conversation. First create a disposable dialogue and get explicit
approval for its destruction.

## Rollback

Stop new git-dialog-taskboard.service and bridge, switch code checkout to
saved P31 HEAD, start bridge. Legacy taskboard may optionally be restarted
on 8787. Do not delete local auth/registry state, old pilot.db, Work config
or event subscriptions. Rollback of Python source does not recover a remote
Git ref that was successfully deleted.


## P32.1 — light theme and history latency

The user-facing taskboard intentionally uses a near-white gray background,
a green History button, blue MT and purple ChatGPT badges with large type,
a prominent "next turn" indicator and a modal history overlay.
The overlay appears IMMEDIATELY on click and shows load status while the
Git authority is fetching immutable snapshots. History pages are newest first.

Each authority view currently verifies the private GitHub repository and
downloads the commit/tree plus ALL message blobs, even to display one last
message. The P32 table was previously issuing one full view per row; on
click, it issued another full view. With sequential reads on the authority
thread this can produce many seconds of latency, especially for dialogues
with long histories. P32.1 shares the initial page of 20 messages between
the table and history overlay, with a bounded, short-lived 15-second browser
cache keyed by task_id. Manual Refresh clears that cache. Cache is ONLY for
read-only display; delete always refetches fresh head, then authority again
validates the remote branch head before preparing and confirming delete.

No performance metric is asserted before operator benchmarks. Read-only
diagnosis should measure authenticated /api/history latency in browser
Network tools, and time three sequential Unix op=view reads with explicit
task_id, offset=0, limit=20, order=newest. Report durations and message
counts only, never credentials, raw logs, HTTP headers or message bodies.

If latency remains unacceptable for a 22-message dialogue, the next possible
improvement is a carefully bounded immutable snapshot cache behind the Unix
authority, keyed by exact Git head, with independent HEAD revalidation. This
requires separate security review/tests; do not silently bypass Git snapshot
checks or weaken delete safety.


## P32.2 — RAM-only cache and bounded GitHub reads

No database or file cache of Git messages. Existing lifecycle, outbox and notification SQLite files continue to hold correctness metadata only.
Each GitHubDialog.load still checks the private repository and live branch HEAD before consulting its 8-entry process-local least-recently-used cache keyed by (task_id, HEAD). A changed head misses; cache vanishes at restart.
Cold snapshots on approved JsonHttp fetch up to four immutable blobs in parallel with thread-local HTTPS openers. Blob Git SHA checks, history validation and all size limits are unchanged. Offline fake transports stay sequential.
Append and delete still use independent live ref checks; never authorize a write from cached history. No automatic retries or transparent recovery.
GitHub secondary rate limits matter. Do not raise concurrency above four without measuring and review.
Deploy the pinned SHA only after full pytest and Node syntax green. Stop bridge, change clean production checkout, restart bridge, then restart taskboard. Do not re-run taskboard init: preserve password verifier and existing local state.
Test read-only: bridge /ping, unauthenticated taskboard HTTP 401, authenticated list/history. Never delete a live task to benchmark.
Benchmark cold/warm same task via local Unix authority: output durations, counts and status, never credentials or message bodies.

## P32.3 — legacy pilot tombstone and immutable notification routing

Git bridge authority startup used to require a LIVE, registered legacy pilot
task id and a readable remote branch. After user deletes its Git branch and
the lifecycle registry retains a tombstone, the service would fail to boot.

The reviewed change in gateway_lifespan.py permits a registered RETIRED pilot
only when an EXISTING P26 journal survives. It independently re-verifies the
private Git repository without reopening the deleted remote ref; the P26
journal's fixed checkpoint and P31's historical pilot exclusion must retain
the ORIGINAL pilot ID. No new checkpoint is minted for a retired pilot.

CRITICAL PRODUCTION MIGRATION REQUIRED (NOT automatically performed): current
production preflight.py contains a tracked uncommitted edit pointing TASK_ID
at task_df06d95a56c65e80d8a2245386cb2e97 rather than the original
task_3c2d37a2485b. Live P26 route.task and P31 route.pilot were also
manually re-pointed to that new active task. Since P26 seq checkpoint=12,
and P31 excludes its route.pilot, MT->ChatGPT automatic notifications for
the active anchor can be omitted. Manual dialog_view still works.

After TESTS GREEN, a separately reviewed, fail-closed maintenance procedure
must (a) take fresh backups of BOTH current journal DBs and lifecycle state,
(b) shut down the bridge and taskboard, (c) restore ONLY the original pilot
identity in the two historical routing columns while preserving head/seq,
checkpoint, owner_hash, events and queued flags, (d) verify both journals
are internally consistent and pending events remain intact, (e) revert ONLY
the known one-line tracked preflight.TASK_ID edit, and (f) switch code to
the validated P32.3 SHA. Restore services and verify /ping 200, :8787 401,
and Git-dialog operations. Never replace entire databases from an old backup
or retry uncertain Git mutations. Never delete real dialogue branches.

Do not deploy based on a unit test alone: tests cannot validate the current
production DB migration state or native event delivery. A successful service
startup is necessary but not sufficient to prove notifications are delivered.

### P32.3 one-shot *two-journal* maintenance tool

Code: src/git_dialog/route_migration.py. Run under the production Python
environment with PYTHONPATH=src and both services STOPPED; this is an
operator-invoked CLI, not an MCP tool, web endpoint or automatic migration.
Do not touch Git commits or history; no message data is modified.

Prerequisite: isolated full pytest and compileall/node --check green at pinned
exact candidate SHA. Production HEAD is P32.2 745e0399948e58f3934d5adca71b0d023ce65528.
Production git status is only one tracked local one-line override of
src/git_dialog/preflight.py, original pilot -> currently active anchor.
Before changing anything, verify that exact one-line diff and save a copy.
STOP both services and verify that the old P32.2 bridge process exited.
Restore only that known one-line override to the original PILOT binding,
then switch detached clean worktree to the vetted P32.3 candidate.

Two required CLI calls from repository root:

    PYTHONPATH=src python -m git_dialog.route_migration plan
    PYTHONPATH=src python -m git_dialog.route_migration apply RESTORE_HISTORICAL_PILOT_P323

The CLI expects the original pilot to be tombstoned with remote_absent=1
and the temporary anchor active, source preflight.TASK_ID equal to the
original pilot, exact historic P26 head and seq=12, P31 checkpoint=19,
matched owner binding, two valid SQLite DBs in rollback DELETE mode and
zero pending event outbox rows. It refuses partial or previously applied
routing. It creates and fsyncs fresh mode-0600 SQLite backups of both
journals in existing private state/backups BEFORE any writes. Updates both
route task/pilot IDs in one SQLite ATTACH transaction. Checks integrity and
event payload fingerprints, owner hashes, checkpoints and counts again
inside and after commit. All other rows remain unchanged.

After success: start ONLY hermes-bridge and git-dialog-taskboard, verify
/ping 200, unauthenticated taskboard 401, authenticated dialog_view (read
only) and the new notification exclusion points to the TOMBSTONED old
pilot, not the active chat. Missed MT notifications may be emitted once
from durable appends; do not claim live event delivery without observation.

If deployment/start/health fails: STOP targeted services, inspect and
preserve new logs/backups; restore only the two migration backups (never
old pre_fix snapshots), verify restored route IDs, switch back to previous
P32.2 SHA, reinstate ONLY the previously verified one-line local TASK_ID
override so old startup works, restart targeted services and check health.
Do not reset/clean other working-tree changes, DBs, Git refs, owner hashes,
messages, credentials, subscriptions or Tailscale. If backups/current
state mismatch, STOP and report without guessed destructive recovery.
