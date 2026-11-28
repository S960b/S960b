import hashlib
import json
import threading

import pytest

from git_dialog import DialogError
from git_dialog.github import ApiError, GitHubDialog
from git_dialog.journal import ExecutionJournal
from git_dialog.lifecycle import LifecycleRegistry
from git_dialog.worker import WorkerStep, evaluate
from test_github import FakeApi, TASK, REF


def action(op="ping", **extra):
    return json.dumps({"protocol": "git-dialog-action", "version": 1, "op": op, **extra})


def setup(tmp_path, body=None, ops=("ping", "sha256")):
    api = FakeApi()
    dialog = GitHubDialog(api)
    receipt = dialog.append(TASK, api.initial, message_id="req", actor="chatgpt", kind="task",
                            body=action() if body is None else body)
    journal = ExecutionJournal(tmp_path / "journal.sqlite")
    registry = LifecycleRegistry(tmp_path / "lifecycle.sqlite")
    registry.register(TASK, receipt.commit_sha)
    worker = WorkerStep(journal, dialog, registry, TASK, allowed_ops=ops)
    api.calls.clear()
    return api, journal, registry, worker


def close(journal, registry):
    journal.close()
    registry.close()


def test_ping_and_default_deny():
    assert evaluate(action(), ("ping",))["value"] == "pong"
    with pytest.raises(DialogError, match="action_not_allowed"):
        evaluate(action())


def test_sha256_is_text_not_path_or_command():
    text = "/etc/passwd; $(do-not-execute)"
    result = evaluate(action("sha256", text=text), ("sha256",))
    assert result["value"] == hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.parametrize("body", [
    action("shell", command="echo nope"), action("ping", command="nope"),
    action("ping", path="/etc/passwd"), action("sha256", text=5),
    action("sha256", text="x" * 2049), "plain shell text", "[]", "null",
    '{"protocol":"git-dialog-action","version":1,"op":"ping","op":"shell"}',
    '{"protocol":"git-dialog-action","version":true,"op":"ping"}',
    "x" * 4097,
])
def test_unsafe_or_malformed_action_is_rejected(body):
    with pytest.raises(DialogError):
        evaluate(body, ("ping", "sha256"))


def test_one_step_then_retry_never_evaluates_again(tmp_path, monkeypatch):
    api, journal, registry, worker = setup(tmp_path)
    result = worker.step("req", "report", "run")
    assert result["status"] == "published"
    assert json.loads(result["result"])["value"] == "pong"
    def forbidden(*a):
        raise AssertionError("must reuse saved report")
    monkeypatch.setattr("git_dialog.worker.evaluate", forbidden)
    api.calls.clear()
    assert worker.step("req", "report", "run") == result
    assert all(c[0] == "GET" for c in api.calls)
    close(journal, registry)


def test_unknown_action_produces_durable_refusal_not_execution(tmp_path):
    api, journal, registry, worker = setup(tmp_path, action("shell", command="secret input"))
    result = worker.step("req", "report", "run")
    body = json.loads(result["result"])
    assert body["ok"] is False and body["error"] == "action_not_allowed"
    assert "secret input" not in result["result"]
    close(journal, registry)


@pytest.mark.parametrize("state", ["cancelled", "closing", "tombstoned"])
def test_inactive_lifecycle_blocks_before_remote_or_journal_start(tmp_path, state):
    api, journal, registry, worker = setup(tmp_path)
    if state == "cancelled":
        registry.cancel(TASK)
    else:
        intent = registry.request_delete(TASK, api.refs[REF], now=10)
        if state == "tombstoned":
            registry.confirm_delete(TASK, intent["confirmation"], api.refs[REF], now=11)
    with pytest.raises(DialogError, match="dialog_not_writable"):
        worker.step("req", "report", "run")
    assert not api.calls
    with pytest.raises(DialogError, match="execution_not_found"):
        journal.get(TASK, "req")
    close(journal, registry)


def test_unknown_registry_entry_is_fail_closed(tmp_path):
    api, journal, registry, worker = setup(tmp_path)
    worker.task_id = "unknown_task"
    with pytest.raises(DialogError, match="unregistered_dialog"):
        worker.step("req", "report", "run")
    assert not api.calls
    close(journal, registry)


@pytest.mark.parametrize("state", ["started", "unknown"])
def test_unresolved_execution_never_restarts(tmp_path, monkeypatch, state):
    api, journal, registry, worker = setup(tmp_path)
    incoming = GitHubDialog(api).load(TASK).last
    journal.receive(incoming)
    assert journal.start(TASK, "req", "run")
    if state == "unknown":
        journal.mark_unknown(TASK, "req", "run")
    def forbidden(*a):
        raise AssertionError("no second execution")
    monkeypatch.setattr("git_dialog.worker.evaluate", forbidden)
    api.calls.clear()
    with pytest.raises(DialogError, match="worker_execution_unresolved"):
        worker.step("req", "report", "run")
    assert all(c[0] == "GET" for c in api.calls)
    close(journal, registry)


def test_lost_ack_then_reopen_observes_without_compute(tmp_path, monkeypatch):
    api, journal, registry, worker = setup(tmp_path)
    real = api.request
    def lost(method, path, payload=None):
        response = real(method, path, payload)
        if method == "PATCH":
            raise ApiError()
        return response
    api.request = lost
    with pytest.raises(DialogError, match="publication_unknown"):
        worker.step("req", "report", "run")
    close(journal, registry)
    j = ExecutionJournal(tmp_path / "journal.sqlite")
    r = LifecycleRegistry(tmp_path / "lifecycle.sqlite")
    reopened = WorkerStep(j, GitHubDialog(api), r, TASK, allowed_ops=("ping",))
    def forbidden(*a):
        raise AssertionError("no recompute")
    monkeypatch.setattr("git_dialog.worker.evaluate", forbidden)
    api.calls.clear()
    assert reopened.step("req", "report", "run")["status"] == "published"
    assert all(c[0] == "GET" for c in api.calls)
    close(j, r)


def test_wrong_run_or_report_id_never_writes(tmp_path):
    api, journal, registry, worker = setup(tmp_path)
    worker.step("req", "report", "run")
    api.calls.clear()
    with pytest.raises(DialogError, match="not_execution_owner"):
        worker.step("req", "report", "other-run")
    with pytest.raises(DialogError, match="worker_report_id_conflict"):
        worker.step("req", "other-report", "run")
    assert all(c[0] == "GET" for c in api.calls)
    close(journal, registry)


def test_no_default_operations_are_enabled(tmp_path):
    api, journal, registry, worker = setup(tmp_path, ops=())
    result = worker.step("req", "report", "run")
    assert json.loads(result["result"])["error"] == "action_not_allowed"
    close(journal, registry)


def test_other_repository_refused(tmp_path):
    api, journal, registry, worker = setup(tmp_path)
    with pytest.raises(DialogError, match="worker_repository_forbidden"):
        WorkerStep(journal, GitHubDialog(api, "S960b/other"), registry, TASK)
    assert not api.calls
    close(journal, registry)


@pytest.mark.parametrize("policy", ["ping", ["shell"], [1]])
def test_invalid_policies_rejected(policy):
    with pytest.raises(DialogError, match="invalid_worker_policy"):
        evaluate(action(), policy)


def test_deep_json_is_bounded_refusal():
    with pytest.raises(DialogError):
        evaluate("[" * 1500 + "0" + "]" * 1500, ("ping",))


def test_worker_hash_result_and_missing_branch(tmp_path):
    api, journal, registry, worker = setup(tmp_path, action("sha256", text="abc"))
    result = worker.step("req", "report", "run")
    assert json.loads(result["result"])["value"] == hashlib.sha256(b"abc").hexdigest()
    api.refs.clear()
    api.calls.clear()
    with pytest.raises(DialogError, match="branch_missing_or_inaccessible"):
        worker.step("req", "report", "run")
    assert all(c[0] == "GET" for c in api.calls)
    close(journal, registry)


def test_internal_crash_leaves_started_not_autoretried(tmp_path, monkeypatch):
    api, journal, registry, worker = setup(tmp_path)
    def crash(*args):
        raise RuntimeError("simulated unexpected compute failure")
    monkeypatch.setattr("git_dialog.worker.evaluate", crash)
    with pytest.raises(RuntimeError):
        worker.step("req", "report", "run")
    assert journal.get(TASK, "req")["status"] == "started"
    with pytest.raises(DialogError, match="worker_execution_unresolved"):
        worker.step("req", "report", "run")
    assert all(c[0] == "GET" for c in api.calls)
    close(journal, registry)


def test_cancel_between_save_and_publish_blocks_remote_write(tmp_path, monkeypatch):
    import git_dialog.worker as module
    api, journal, registry, worker = setup(tmp_path)
    real = module.publish_saved
    def cancelled(*args):
        registry.cancel(TASK)
        return real(*args)
    monkeypatch.setattr(module, "publish_saved", cancelled)
    with pytest.raises(DialogError, match="dialog_not_writable"):
        worker.step("req", "report", "run")
    assert journal.get(TASK, "req")["status"] == "result_ready"
    assert all(c[0] == "GET" for c in api.calls)
    close(journal, registry)


def test_two_workers_only_one_computation_and_patch(tmp_path, monkeypatch):
    import git_dialog.worker as module
    api, journal, registry, worker = setup(tmp_path)
    close(journal, registry)
    real = module.evaluate
    computes, outcomes, errors = [], [], []
    barrier = threading.Barrier(2)
    def counted(*args):
        computes.append(1)
        return real(*args)
    monkeypatch.setattr(module, "evaluate", counted)
    def run():
        j = ExecutionJournal(tmp_path / "journal.sqlite")
        r = LifecycleRegistry(tmp_path / "lifecycle.sqlite")
        try:
            w = WorkerStep(j, GitHubDialog(api), r, TASK, allowed_ops=("ping",))
            barrier.wait(timeout=3)
            try:
                outcomes.append(w.step("req", "report", "run")["status"])
            except DialogError as exc:
                outcomes.append(exc.code)
        except BaseException as exc:
            errors.append(exc)
        finally:
            close(j, r)
    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
        assert not t.is_alive()
    assert not errors
    assert "published" in outcomes
    assert set(outcomes) <= {"published", "publication_unresolved"}
    assert len(computes) == 1
    assert len([c for c in api.calls if c[0] == "PATCH"]) == 1
