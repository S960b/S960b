import json
import sqlite3
import threading

import pytest

from git_dialog import DialogError, Message
from git_dialog.github import ApiError, GitHubDialog
from git_dialog.journal import ExecutionJournal
from git_dialog.publication import publish_saved
from test_github import FakeApi, TASK, REF, STAMP


def ready(tmp_path):
    api = FakeApi()
    dialog = GitHubDialog(api)
    receipt = dialog.append(TASK, api.initial, message_id="request", actor="chatgpt",
                            kind="task", body="DATA ONLY, no execution", created_at_utc=STAMP)
    incoming = receipt.message
    report = Message(TASK, "saved-report", 2, "mt", "chatgpt", "request", "report",
                     STAMP, "fixed precomputed test result")
    path = tmp_path / "journal.sqlite"
    journal = ExecutionJournal(path)
    journal.receive(incoming)
    assert journal.start(TASK, "request", "run-one")
    journal.save_result(TASK, "request", "run-one", report)
    api.calls.clear()
    return api, dialog, journal, path, report


def publish(journal, dialog):
    return publish_saved(journal, dialog, TASK, "request", "run-one")


def mutations(api):
    return [c for c in api.calls if c[0] != "GET"]


def test_saved_report_published_then_repeated_read_only(tmp_path):
    api, dialog, journal, path, report = ready(tmp_path)
    row = publish(journal, dialog)
    assert row["status"] == "published" and row["publication_attempted"] == 1
    assert dialog.load(TASK).last == report
    api.calls.clear()
    assert publish(journal, dialog)["commit_sha"] == row["commit_sha"]
    assert not mutations(api)
    assert not journal.start(TASK, "request", "another-run")
    journal.close()


def test_accept_then_lost_ack_restart_only_observes(tmp_path):
    api, dialog, journal, path, report = ready(tmp_path)
    real = api.request
    def lost(method, path, payload=None):
        response = real(method, path, payload)
        if method == "PATCH":
            raise ApiError()
        return response
    api.request = lost
    with pytest.raises(DialogError, match="publication_unknown"):
        publish(journal, dialog)
    assert journal.get(TASK, "request")["status"] == "result_ready"
    journal.close()
    api.calls.clear()
    reopened = ExecutionJournal(path)
    assert publish(reopened, dialog)["status"] == "published"
    assert not mutations(api)
    assert not reopened.start(TASK, "request", "run-one")
    reopened.close()


def test_crash_after_reservation_before_send_no_resend(tmp_path):
    api, dialog, journal, path, report = ready(tmp_path)
    assert journal.reserve_publication(TASK, "request", "run-one")
    journal.close()
    reopened = ExecutionJournal(path)
    with pytest.raises(DialogError, match="publication_unresolved"):
        publish(reopened, dialog)
    assert not mutations(api)
    assert not reopened.start(TASK, "request", "new-run")
    reopened.close()


def test_crash_after_observed_before_local_mark(tmp_path, monkeypatch):
    api, dialog, journal, path, report = ready(tmp_path)
    def crash(*args):
        raise RuntimeError("simulated local failure")
    monkeypatch.setattr(journal, "mark_published", crash)
    with pytest.raises(RuntimeError):
        publish(journal, dialog)
    journal.close()
    api.calls.clear()
    reopened = ExecutionJournal(path)
    assert publish(reopened, dialog)["status"] == "published"
    assert not mutations(api)
    reopened.close()


def test_wrong_run_no_remote_io(tmp_path):
    api, dialog, journal, path, report = ready(tmp_path)
    with pytest.raises(DialogError, match="not_execution_owner"):
        publish_saved(journal, dialog, TASK, "request", "wrong-run")
    assert not api.calls
    journal.close()


def test_missing_branch_no_mutation_or_reservation(tmp_path):
    api, dialog, journal, path, report = ready(tmp_path)
    api.refs.clear()
    with pytest.raises(DialogError, match="branch_missing_or_inaccessible"):
        publish(journal, dialog)
    assert not mutations(api)
    assert journal.get(TASK, "request")["publication_attempted"] == 0
    journal.close()


def test_conflicting_remote_result_no_local_success(tmp_path):
    api, dialog, journal, path, report = ready(tmp_path)
    snap = dialog.load(TASK)
    dialog.append(TASK, snap.head, message_id=report.message_id, actor="mt", kind="report",
                  body="different", created_at_utc=STAMP)
    api.calls.clear()
    with pytest.raises(DialogError, match="result_conflict"):
        publish(journal, dialog)
    assert not mutations(api)
    assert journal.get(TASK, "request")["status"] == "result_ready"
    journal.close()


def test_unrelated_remote_tail_cannot_publish_saved_wrong_seq(tmp_path):
    api, dialog, journal, path, report = ready(tmp_path)
    snap = dialog.load(TASK)
    r = dialog.append(TASK, snap.head, message_id="other-report", actor="mt", kind="report", body="other")
    dialog.append(TASK, r.commit_sha, message_id="next-request", actor="chatgpt", kind="task", body="other")
    api.calls.clear()
    with pytest.raises(DialogError, match="result_does_not_match_remote_turn"):
        publish(journal, dialog)
    assert not mutations(api)
    journal.close()


def test_published_result_missing_remote_never_resends(tmp_path):
    api, dialog, journal, path, report = ready(tmp_path)
    old = api.refs[REF]
    publish(journal, dialog)
    api.refs[REF] = old  # simulate EXTERNAL forbidden rewind
    api.calls.clear()
    with pytest.raises(DialogError, match="published_report_missing"):
        publish(journal, dialog)
    assert not mutations(api)
    journal.close()


def test_two_publication_reservations_one_winner(tmp_path):
    api, dialog, journal, path, report = ready(tmp_path)
    journal.close()
    barrier = threading.Barrier(2)
    results = []
    def worker():
        j = ExecutionJournal(path)
        try:
            barrier.wait(timeout=3)
            results.append(j.reserve_publication(TASK, "request", "run-one"))
        finally:
            j.close()
    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
        assert not t.is_alive()
    assert sorted(results) == [False, True]


def test_migrates_original_journal_schema_without_losing_result(tmp_path):
    api, dialog, journal, path, report = ready(tmp_path)
    journal.close()
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE executions DROP COLUMN publication_attempted")
    conn.commit()
    conn.close()
    reopened = ExecutionJournal(path)
    assert json.loads(reopened.get(TASK, "request")["result"]) == report.to_dict()
    assert reopened.get(TASK, "request")["publication_attempted"] == 1
    with pytest.raises(DialogError, match="publication_unresolved"):
        publish(reopened, dialog)
    assert not mutations(api)
    reopened.close()


def test_started_without_result_cannot_publish(tmp_path):
    api, dialog, journal, path, report = ready(tmp_path)
    journal.conn.execute("UPDATE executions SET status='started',result=NULL")
    journal.conn.commit()
    with pytest.raises(DialogError, match="result_not_ready"):
        publish(journal, dialog)
    assert not api.calls
    journal.close()


def test_remote_input_payload_mismatch_refuses_publish(tmp_path):
    api, dialog, journal, path, report = ready(tmp_path)
    row = journal.get(TASK, "request")
    payload = json.loads(row["payload"])
    payload["body"] = "different local input"
    journal.conn.execute("UPDATE executions SET payload=?", (json.dumps(payload),))
    journal.conn.commit()
    with pytest.raises(DialogError, match="remote_input_mismatch"):
        publish(journal, dialog)
    assert not mutations(api)
    journal.close()


def test_coordinator_race_only_one_patch(tmp_path):
    api, dialog, journal, path, report = ready(tmp_path)
    journal.close()
    barrier = threading.Barrier(2)
    outcomes = []
    def worker():
        j = ExecutionJournal(path)
        try:
            barrier.wait(timeout=3)
            try:
                outcomes.append(publish(j, GitHubDialog(api))["status"])
            except DialogError as exc:
                outcomes.append(exc.code)
        finally:
            j.close()
    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
        assert not t.is_alive()
    assert len(outcomes) == 2 and "published" in outcomes
    assert set(outcomes) <= {"published", "publication_unresolved"}
    assert len([c for c in api.calls if c[0] == "PATCH"]) == 1


@pytest.mark.parametrize("status", [403, 422, 500])
def test_rejected_send_stays_durable_and_never_retries(tmp_path, status):
    api, dialog, journal, path, report = ready(tmp_path)
    def fail(method, *args):
        if method == "PATCH":
            raise ApiError(status)
    api.hook = fail
    with pytest.raises(DialogError):
        publish(journal, dialog)
    assert journal.get(TASK, "request")["status"] == "result_ready"
    journal.close()
    api.calls.clear()
    reopened = ExecutionJournal(path)
    with pytest.raises(DialogError, match="publication_unresolved"):
        publish(reopened, dialog)
    assert not mutations(api)
    reopened.close()


def test_success_shaped_ack_without_remote_result_cannot_mark_published(tmp_path):
    api, dialog, journal, path, report = ready(tmp_path)
    real = api.request
    def lie(method, path, payload=None):
        if method == "PATCH":
            return {"ref": REF, "object": {"sha": payload["sha"]}}
        return real(method, path, payload)
    api.request = lie
    with pytest.raises(DialogError, match="publication_not_observed"):
        publish(journal, dialog)
    assert journal.get(TASK, "request")["status"] == "result_ready"
    assert journal.get(TASK, "request")["publication_attempted"] == 1
    journal.close()


def test_ack_and_report_are_not_executable_tasks(tmp_path):
    api, dialog, journal, path, report = ready(tmp_path)
    with pytest.raises(DialogError, match="not_executable_task"):
        journal.receive(report)
    journal.close()
