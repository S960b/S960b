"""Three separate processes: prepare durable data, send once, observe on restart.

Only a new, isolated private SQLite file and fixed non-executable canary data.
No evaluator, model, shell, worker, branch creation/deletion, or automatic retry.
Historical offline fixture only; credential-taking CLI is disabled.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

from . import DialogError, Message, parse_message
from .github import ApiError, GitHubDialog, JsonHttp, sha
from .journal import ExecutionJournal
from .live_probe import ProbeTransport
from .preflight import BASE, MAIN_SHA, TASK_ID, ReadOnlyTransport, read_configured_token
from .publication import publish_saved

REQUEST_ID = "journal_request_v1"
REQUEST_BODY = "GIT_JOURNAL_REQUEST_V1_NO_EXECUTION"
REPORT_ID = "journal_report_v1"
REPORT_BODY = "GIT_JOURNAL_REPORT_V1_PRECOMPUTED_NO_EXECUTION"
RUN_ID = "journal_canary_run_v1"


def _db_file(path, create):
    path = Path(path)
    parent = path.parent
    try:
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) != 0o700):
            raise DialogError("isolated_directory_required")
        if create:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) != 0o600):
            raise DialogError("isolated_database_required")
    except OSError:
        raise DialogError("isolated_database_unavailable") from None
    return path


def _main_unchanged(transport):
    obj = transport.request("GET", BASE + "/git/ref/heads/main")
    if obj.get("ref") != "refs/heads/main" or obj.get("object", {}).get("sha") != MAIN_SHA:
        raise DialogError("main_checkpoint_changed")


def _input(snapshot):
    if (snapshot.task.get("mode") != "transport_canary_only" or
            len(snapshot.messages) not in (3, 4) or snapshot.state.status != "working"):
        raise DialogError("unexpected_journal_probe_snapshot")
    m = snapshot.messages[2]
    if (m.task_id != TASK_ID or m.message_id != REQUEST_ID or m.seq != 3 or
            m.sender != "chatgpt" or m.recipient != "mt" or m.kind != "task" or
            m.reply_to != "mt_canary_v1" or m.body != REQUEST_BODY):
        raise DialogError("unexpected_journal_probe_input")
    return m


def run_probe(transport, database, initial_head, action):
    sha(initial_head)
    if action not in ("prepare", "send", "observe"):
        raise DialogError("invalid_journal_probe_action")
    guard = ProbeTransport(transport) if action == "send" else ReadOnlyTransport(transport)
    _main_unchanged(guard)
    dialog = GitHubDialog(guard)
    snapshot = dialog.load(TASK_ID)
    incoming = _input(snapshot)
    if len(snapshot.messages) == 3 and snapshot.head != initial_head:
        raise DialogError("unexpected_journal_probe_head")
    if action == "prepare" and len(snapshot.messages) != 3:
        raise DialogError("unexpected_journal_probe_head")
    if len(snapshot.messages) == 4:
        commit = dialog._commit(snapshot.head)
        parents = commit.get("parents")
        if (not isinstance(parents, list) or len(parents) != 1 or
                not isinstance(parents[0], dict) or parents[0].get("sha") != initial_head):
            raise DialogError("unexpected_journal_probe_parent")
    path = _db_file(database, create=action == "prepare")
    journal = ExecutionJournal(path, actor="mt")
    try:
        if action == "prepare":
            journal.receive(incoming)
            if not journal.start(TASK_ID, REQUEST_ID, RUN_ID):
                raise DialogError("unexpected_journal_probe_start")
            # Pure, precomputed TEST DATA. No body is evaluated or executed.
            from datetime import datetime, timezone
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            result = Message(TASK_ID, REPORT_ID, 4, "mt", "chatgpt", REQUEST_ID,
                             "report", timestamp, REPORT_BODY)
            row = journal.save_result(TASK_ID, REQUEST_ID, RUN_ID, result)
        else:
            row = journal.get(TASK_ID, REQUEST_ID)
            result = parse_message(json.loads(row["result"]))
            if (row["run_id"] != RUN_ID or row["payload"] !=
                    json.dumps(incoming.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) or
                    result.message_id != REPORT_ID or result.seq != 4 or
                    result.kind != "report" or result.body != REPORT_BODY):
                raise DialogError("unexpected_journal_probe_result")
            if action == "observe" and not row["publication_attempted"]:
                raise DialogError("observation_requires_previous_send_attempt")
            row = publish_saved(journal, dialog, TASK_ID, REQUEST_ID, RUN_ID)
        _main_unchanged(guard)
        return {"task_id": TASK_ID, "action": action, "status": row["status"],
                "publication_attempted": row["publication_attempted"],
                "confirmed_commit": row["commit_sha"], "report_message_id": REPORT_ID,
                "result_sha256": hashlib.sha256(row["result"].encode("utf-8")).hexdigest(),
                "execution": "none_precomputed_data_only", "main_sha": MAIN_SHA}
    finally:
        journal.close()


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 4:
        print('{"error":"expected_action_local_credential_file_new_database_initial_head"}')
        return 2
    try:
        action, credential, database, initial_head = args
        if action not in ("prepare", "send", "observe"):
            raise DialogError("invalid_journal_probe_action")
        sha(initial_head)
        raise DialogError('legacy_direct_writer_disabled_use_authority_writers')
    except ApiError as exc:
        result = {"error": "github_http_error", "status": exc.status}
    except DialogError as exc:
        result = {"error": exc.code}
    except Exception:
        result = {"error": "journal_probe_failed"}
    print(json.dumps(result, sort_keys=True))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    raise SystemExit(main())

