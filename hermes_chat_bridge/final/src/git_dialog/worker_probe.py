"""Isolated one-shot ping canary: prepare, step ONCE, GET-only observe.

No polling, service installation, arbitrary action, ref lifecycle or old DBs.
Historical offline fixture only; credential-taking CLI is disabled. Live worker
steps use authority_writers worker, never this fixed-sequence historical probe.
"""
import hashlib
import json
from pathlib import Path
import sys

from . import DialogError, parse_message
from .github import ApiError, GitHubDialog, JsonHttp, canonical, sha
from .journal import ExecutionJournal, _wire
from .journal_probe import _db_file, _main_unchanged
from .lifecycle import LifecycleDialog, LifecycleRegistry
from .live_probe import ProbeTransport
from .preflight import MAIN_SHA, TASK_ID, ReadOnlyTransport, read_configured_token
from .publication import publish_saved
from .worker import WorkerStep

PREVIOUS_HEAD = "b2d000797fa14072b76fdb6fb1ded9ac4f9638d1"
REQUEST_ID = "worker_request_v1"
REPORT_ID = "worker_report_v1"
RUN_ID = "worker_canary_run_v1"
REQUEST_BODY = '{"protocol":"git-dialog-action","version":1,"op":"ping"}'
RESULT_BODY = canonical({"protocol": "git-dialog-result", "version": 1,
                         "ok": True, "op": "ping", "value": "pong"})


def _parent(dialog, head, expected):
    parents = dialog._commit(head).get("parents")
    # Real GitHub includes url/html_url alongside sha; validate one parent SHA.
    if (not isinstance(parents, list) or len(parents) != 1 or
            not isinstance(parents[0], dict) or parents[0].get("sha") != expected):
        raise DialogError("unexpected_worker_probe_parent")


def _only_append(dialog, old_head, new_head, message_path):
    def files(head):
        commit = dialog._commit(head)
        return {p: e for p, e in dialog._tree(commit["tree"]["sha"]).items()
                if e["type"] != "tree"}
    old, new = files(old_head), files(new_head)
    changed = {p for p in old.keys() | new.keys() if old.get(p) != new.get(p)}
    if changed != {"state.json", message_path} or message_path in old:
        raise DialogError("unexpected_worker_probe_files")


def _snapshots(dialog, initial_head):
    snapshot = dialog.load(TASK_ID)
    initial = dialog._snapshot(TASK_ID, initial_head)[0]
    previous = dialog._snapshot(TASK_ID, PREVIOUS_HEAD)[0]
    _parent(dialog, initial_head, PREVIOUS_HEAD)
    if (len(previous.messages) != 4 or previous.last.message_id != "journal_report_v1" or
            initial.task != previous.task or initial.task.get("mode") != "transport_canary_only" or
            len(initial.messages) != 5 or initial.messages[:4] != previous.messages or
            initial.state.revision != 5 or initial.state.next_actor != "mt" or
            initial.state.status != "working"):
        raise DialogError("unexpected_worker_probe_request")
    incoming = initial.last
    if (incoming.message_id != REQUEST_ID or incoming.seq != 5 or
            incoming.sender != "chatgpt" or incoming.recipient != "mt" or
            incoming.kind != "task" or incoming.reply_to != "journal_report_v1" or
            incoming.body != REQUEST_BODY):
        raise DialogError("unexpected_worker_probe_request")
    _only_append(dialog, PREVIOUS_HEAD, initial_head, "messages/5.chatgpt.json")
    if (snapshot.task != initial.task or snapshot.messages[:5] != initial.messages or
            len(snapshot.messages) not in (5, 6) or snapshot.state.status != "working"):
        raise DialogError("unexpected_worker_probe_snapshot")
    if len(snapshot.messages) == 5:
        if snapshot.head != initial_head:
            raise DialogError("unexpected_worker_probe_head")
    else:
        _parent(dialog, snapshot.head, initial_head)
        _only_append(dialog, initial_head, snapshot.head, "messages/6.mt.json")
    return snapshot, incoming


def _saved(row, incoming):
    if (row["payload"] != _wire(incoming) or row["run_id"] != RUN_ID or
            row["status"] not in ("result_ready", "published") or not row["result"]):
        raise DialogError("unexpected_worker_probe_result")
    report = parse_message(json.loads(row["result"]))
    if (report.task_id != TASK_ID or report.message_id != REPORT_ID or report.seq != 6 or
            report.sender != "mt" or report.recipient != "chatgpt" or report.kind != "report" or
            report.reply_to != REQUEST_ID or report.body != RESULT_BODY):
        raise DialogError("unexpected_worker_probe_result")
    return report


def run_probe(transport, directory, initial_head, action):
    sha(initial_head)
    if action not in ("prepare", "step", "observe"):
        raise DialogError("invalid_worker_probe_action")
    guard = ProbeTransport(transport) if action == "step" else ReadOnlyTransport(transport)
    _main_unchanged(guard)
    dialog = GitHubDialog(guard)
    snapshot, incoming = _snapshots(dialog, initial_head)
    if action == "prepare" and len(snapshot.messages) != 5:
        raise DialogError("unexpected_worker_probe_head")
    directory = Path(directory)
    # Both files must be absent for prepare. Never attach to or migrate old DBs.
    paths = [directory / "journal.sqlite", directory / "lifecycle.sqlite"]
    if action == "prepare" and any(p.exists() or p.is_symlink() for p in paths):
        raise DialogError("worker_probe_requires_new_databases")
    for path in paths:
        _db_file(path, create=action == "prepare")
    registry = LifecycleRegistry(paths[1])
    try:
        journal = ExecutionJournal(paths[0], actor="mt")
        try:
            if action == "prepare":
                registry.register(TASK_ID, initial_head)  # Local only, not Git create.
                row = journal.receive(incoming)  # Does NOT start/evaluate anything.
            else:
                local = registry.get(TASK_ID)
                if local["state"] != "active" or local["initial_head"] != initial_head:
                    raise DialogError("unexpected_worker_probe_lifecycle")
                row = journal.get(TASK_ID, REQUEST_ID)
                if action == "step":
                    # This CLI never re-runs a step after a partial or complete attempt.
                    if row["status"] != "received" or row["publication_attempted"]:
                        raise DialogError("worker_probe_step_already_started")
                    if row["payload"] != _wire(incoming) or len(snapshot.messages) != 5:
                        raise DialogError("unexpected_worker_probe_input")
                    WorkerStep(journal, dialog, registry, TASK_ID, allowed_ops=("ping",)).step(
                        REQUEST_ID, REPORT_ID, RUN_ID)
                    row = journal.get(TASK_ID, REQUEST_ID)
                else:
                    _saved(row, incoming)
                    if not row["publication_attempted"]:
                        raise DialogError("observation_requires_previous_send_attempt")
                    # No WorkerStep/start/evaluate in restart observation, even on errors.
                    row = publish_saved(journal, LifecycleDialog(dialog, registry),
                                        TASK_ID, REQUEST_ID, RUN_ID)
                report = _saved(row, incoming)
                observed, _ = _snapshots(dialog, initial_head)
                if (len(observed.messages) != 6 or observed.last != report or
                        observed.state.revision != 6 or observed.state.next_actor != "chatgpt" or
                        row["commit_sha"] != observed.head):
                    raise DialogError("worker_probe_report_not_confirmed")
            _main_unchanged(guard)
            return {"task_id": TASK_ID, "action": action, "status": row["status"],
                    "publication_attempted": row["publication_attempted"],
                    "confirmed_commit": row["commit_sha"], "report_message_id": REPORT_ID,
                    "input_sha256": hashlib.sha256(row["payload"].encode()).hexdigest(),
                    "result_sha256": hashlib.sha256(row["result"].encode()).hexdigest()
                        if row["result"] else None,
                    "action_policy": "fixed_ping_only", "main_sha": MAIN_SHA}
        finally:
            journal.close()
    finally:
        registry.close()


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 4:
        print('{"error":"expected_action_local_credential_file_new_directory_initial_head"}')
        return 2
    try:
        action, credential, directory, initial_head = args
        if action not in ("prepare", "step", "observe"):
            raise DialogError("invalid_worker_probe_action")
        sha(initial_head)
        raise DialogError('legacy_direct_writer_disabled_use_authority_writers')
    except ApiError as exc:
        result = {"error": "github_http_error", "status": exc.status}
    except DialogError as exc:
        result = {"error": exc.code}
    except Exception:
        result = {"error": "worker_probe_failed"}
    print(json.dumps(result, sort_keys=True))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    raise SystemExit(main())
