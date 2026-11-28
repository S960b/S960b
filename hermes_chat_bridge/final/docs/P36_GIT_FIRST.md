# P36: Git-first, Hermes MCP metadata-only

## Contract

The private Git branch `dialogs/<task_id>` is the only authority for message
content and turn state. Hermes MCP carries **no message body**. It transports
only IDs, sequence numbers, commit SHAs, and lifecycle metadata. The MT plugin
wakes on a local metadata file and falls back to a 60-second direct Git poll.

This branch is based on the deployed P35 commit
`edca980ec8dddff00d625fff444a4d9a9ffd61c6`, **not** the separate main
branch. P35 remains deployed until isolated P36 acceptance passes.

## Local MT adapter

Commands use existing locally configured PAT (mode 0600) and existing registry:

```sh
PYTHONPATH=src python -m git_dialog.direct_git list --offset 0 --limit 20
PYTHONPATH=src python -m git_dialog.direct_git peek --task-id task_...
PYTHONPATH=src python -m git_dialog.direct_git read --task-id task_... --seq 1 --message-id msg_1
PYTHONPATH=src python -m git_dialog.direct_git append --task-id task_... --head <40hex> --message-id mt_report_1 --kind report --body-file /path/to/local-body.txt
```

The body file is read locally on MT; neither the file path nor body is sent
to Hermes. The local CLI returns only receipt metadata for append and metadata
for peek/list. `read` returns body to the **local plugin** over its own
subprocess pipe, never to MCP. Never paste API keys into body or logs.

## ChatGPT direct Git publishing

ChatGPT obtains the current `head` from `dialog_index` (metadata only),
then reads the immutable `task.json`, `state.json` and predecessor message
from the private Git ref using the GitHub connector. For a new turn:

1. Validate `next_actor == "chatgpt"`, local lifecycle active and current
   head unchanged. Validate the previous `message_id`, `seq`, `revision`.
2. Construct **one** message `messages/<seq>.chatgpt.json` with fields
   `protocol=git-dialog`, `version=1`, `task_id`, `message_id`, `seq`,
   `from=chatgpt`, `to=mt`, `reply_to`, `kind`, `created_at_utc`,
   `body`. Respect the existing 64 KiB Git body limit.
3. Construct the updated `state.json` with `revision+1`, `seq+1`,
   `last_message_id`, `next_actor=mt`, and `status=working` (or the
   transition in `prepare_append`).
4. Use the GitHub connector's Git Data operations to make **one tree**
   based on the old tree SHA containing both files; create one commit
   with the old HEAD as sole parent; non-force update the same
   `dialogs/<task_id>` ref with expected old SHA. Never rebase or force.
5. Independently read back the actual ref/commit and the exact immutable
   message. Only after verified publication call MCP
   `dialog_notice(task_id, head=<commit>, message_id=<id>, seq=<seq>)`.
   The notice verifies the candidate commit is an ancestor of the live
   branch and triggers an optional local wake file for MT.
6. On ambiguous Git publication, **GET-only reconciliation** by the same
   immutable message ID; never blindly republish or change the ID.

No Git write is performed in `dialog_notice`.

## Existing MT plugin adaptation

Source: `deploy/mt-git-inbox-p36.mjs`. Install only after tests. The plugin
runs the direct Git CLI for `list`, `peek` and `read`, not the Unix
authority CLI. It retains the prior durable `seen` and `inflight`
reservations to avoid duplicate prompts after SDK uncertainty.

A successful Git commit is noticed by `local_wake.py` writing only
`{task_id,head,message_id,seq}` into
`~/.local/state/mt-git-inbox/p36-notice.json` with atomic replacement.
Node's `fs.watch` triggers a new scan. The 60-second timer remains an
independent recovery mechanism, so lost wake signals cannot lose messages.

## Inbound MT notifications to ChatGPT

`direct_events.py` scans **Git refs**, not `append_store`. Each newly
observed MT commit is verified against the immutable Git history and queued
with its exact commit SHA. Its per-task `(head,seq)` checkpoint and
notification outbox insertion share a SQLite transaction; native delivery
remains idempotent. No event contains body text.

The new journal starts by checkpointing existing active dialogue heads to
avoid replaying old reports, as permitted for P36 migration. Future new
dialogues begin at seq 0 and are scanned normally.

## Acceptance gates

- Python: `pytest -q tests/test_p36_direct_git.py` and full `pytest -q tests/`.
- Node: `node --check deploy/mt-git-inbox-p36.mjs`; isolated plugin tests
  with fake Git CLI; ensure no Unix socket use for read/list.
- New isolated dialogue: independently publish ChatGPT message in Git using
  GitHub connector; invoke metadata-only `dialog_notice`; observe exactly
  one MT plugin wake and report; verify message content traveled solely
  through Git and local MT reader.
- Failure cases: competing writers, stale head, broken candidate ancestry,
  lost notice, plugin restart, duplicate notice, offline Git, deleted task.
- Security checks: no PAT in logs, no body in MCP tool schemas or event
  payloads, no production branch rewrite.

## Deployment

Use a separate staged worktree for integration. Preserve current P35 service
until all gates pass. A controlled service/plugin restart and live verification
are required; do not assume changing a Git branch updates running Python
imports or the plugin's JavaScript in the application.

Known P36 transition: legacy Git appendix/store and old Unix CLI paths remain
inside the repository for Taskboard and rollback, but no public P36 MCP
method accepts or returns message bodies. They can be removed in later cleanup.
