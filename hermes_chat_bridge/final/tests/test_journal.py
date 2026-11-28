from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading
import pytest
from git_dialog import DialogError, Message
from git_dialog.journal import ExecutionJournal

TASK = "task_demo_1"
INCOMING = Message(TASK, "in1", 1, "chatgpt", "mt", None, "task", "2026-10-26T00:00:00Z", "test only")
RESULT = Message(TASK, "out1", 2, "mt", "chatgpt", "in1", "report", "2026-10-26T00:00:01Z", "completed")


def test_durable_roundtrip_and_no_execution_on_reopen(tmp_path):
    path = tmp_path / "journal.db"
    j = ExecutionJournal(path)
    assert j.receive(INCOMING)["status"] == "received"
    assert j.start(TASK, "in1", "run1") is True
    assert j.start(TASK, "in1", "run1") is False
    j.save_result(TASK, "in1", "run1", RESULT)
    j.close()
    j = ExecutionJournal(path)
    row = j.receive(INCOMING)
    assert row["status"] == "result_ready"
    assert json.loads(row["result"])["message_id"] == "out1"
    assert j.start(TASK, "in1", "run2") is False
    j.mark_published(TASK, "in1", "run1", "a" * 40)
    j.save_result(TASK, "in1", "run1", RESULT)
    assert j.mark_published(TASK, "in1", "run1", "a" * 40)["status"] == "published"
    j.close()
    assert path.stat().st_mode & 0o777 == 0o600


def test_receive_is_idempotent_but_payload_conflict_rejected(tmp_path):
    j = ExecutionJournal(tmp_path / "j.db")
    j.receive(INCOMING)
    j.receive(INCOMING)
    with pytest.raises(DialogError, match="message_id_conflict"):
        j.receive(replace(INCOMING, body="different"))
    assert j.conn.execute("SELECT count(*) FROM executions").fetchone()[0] == 1
    j.close()


def test_only_one_concurrent_start(tmp_path):
    path = tmp_path / "j.db"
    j = ExecutionJournal(path)
    j.receive(INCOMING)
    j.close()
    barrier = threading.Barrier(2)

    def attempt(n):
        journal = ExecutionJournal(path)
        try:
            barrier.wait(timeout=5)
            return journal.start(TASK, "in1", "run" + str(n))
        finally:
            journal.close()

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(attempt, [1, 2])) == [False, True]


def test_crash_unknown_never_authorizes_retry(tmp_path):
    path = tmp_path / "j.db"
    j = ExecutionJournal(path)
    j.receive(INCOMING)
    j.start(TASK, "in1", "run1")
    j.close()  # simulated worker crash after an uncertain external effect
    j = ExecutionJournal(path)
    assert j.start(TASK, "in1", "run2") is False
    assert j.get(TASK, "in1")["status"] == "started"
    assert j.mark_unknown(TASK, "in1", "run1")["status"] == "unknown"
    assert j.start(TASK, "in1", "run1") is False
    with pytest.raises(DialogError, match="invalid_execution_transition"):
        j.save_result(TASK, "in1", "run1", RESULT)
    j.close()


def test_wrong_run_cannot_complete_or_publish(tmp_path):
    j = ExecutionJournal(tmp_path / "j.db")
    j.receive(INCOMING)
    j.start(TASK, "in1", "run1")
    for action in (lambda: j.save_result(TASK, "in1", "other", RESULT),
                   lambda: j.mark_published(TASK, "in1", "other", "a" * 40),
                   lambda: j.mark_unknown(TASK, "in1", "other")):
        with pytest.raises(DialogError, match="not_execution_owner"):
            action()
    j.close()


def test_result_and_publication_are_immutable(tmp_path):
    j = ExecutionJournal(tmp_path / "j.db")
    j.receive(INCOMING)
    j.start(TASK, "in1", "run1")
    j.save_result(TASK, "in1", "run1", RESULT)
    with pytest.raises(DialogError, match="result_conflict"):
        j.save_result(TASK, "in1", "run1", replace(RESULT, body="changed"))
    j.mark_published(TASK, "in1", "run1", "a" * 40)
    with pytest.raises(DialogError, match="publication_conflict"):
        j.mark_published(TASK, "in1", "run1", "b" * 40)
    j.close()


@pytest.mark.parametrize("result", [replace(RESULT, task_id="task_other"),
    replace(RESULT, reply_to="other"), replace(RESULT, seq=3),
    replace(RESULT, kind="task"), replace(RESULT, message_id="in1")])
def test_invalid_result_does_not_change_state(tmp_path, result):
    j = ExecutionJournal(tmp_path / "j.db")
    j.receive(INCOMING)
    j.start(TASK, "in1", "run1")
    with pytest.raises(DialogError):
        j.save_result(TASK, "in1", "run1", result)
    assert j.get(TASK, "in1")["status"] == "started"
    j.close()


def test_publish_requires_durable_result(tmp_path):
    j = ExecutionJournal(tmp_path / "j.db")
    j.receive(INCOMING)
    j.start(TASK, "in1", "run1")
    with pytest.raises(DialogError, match="invalid_execution_transition"):
        j.mark_published(TASK, "in1", "run1", "a" * 40)
    j.close()


def test_reject_foreign_database_without_modifying_it(tmp_path):
    path = tmp_path / "other.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE important (data TEXT)")
    conn.execute("INSERT INTO important VALUES ('preserve')")
    conn.commit()
    with pytest.raises(DialogError, match="wrong_journal_database"):
        ExecutionJournal(path)
    assert conn.execute("PRAGMA application_id").fetchone()[0] == 0
    assert conn.execute("SELECT data FROM important").fetchone()[0] == "preserve"
    conn.close()


def test_report_not_treated_as_new_execution(tmp_path):
    j = ExecutionJournal(tmp_path / "j.db", actor="chatgpt")
    with pytest.raises(DialogError, match="not_executable_task"):
        j.receive(RESULT)
    j.close()
