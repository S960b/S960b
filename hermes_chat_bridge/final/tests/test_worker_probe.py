"""Offline canary processes, using real SQLite only under tmp_path."""
import json

import pytest

from git_dialog import DialogError
from git_dialog.github import ApiError, GitHubDialog, canonical
from git_dialog.journal import ExecutionJournal
from git_dialog.lifecycle import LifecycleRegistry
import git_dialog.worker as worker
import git_dialog.worker_probe as probe
import git_dialog.live_probe as live
from test_github import FakeApi, TASK, REF, STAMP


def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(probe, "TASK_ID", TASK)
    monkeypatch.setattr(live, "TASK_ID", TASK)
    api = FakeApi()
    tree = dict(api.trees[api.commits[api.initial]["tree"]["sha"]])
    tree["task.json"] = api.entry(canonical({"protocol": "git-dialog", "version": "1",
        "task_id": TASK, "title": "offline", "mode": "transport_canary_only"}))
    head = api.commit(api.tree(tree), [api.initial])
    api.refs[REF] = head
    dialog = GitHubDialog(api)
    entries = [("chatgpt_canary_v1", "chatgpt", "ack", live.FIRST_BODY),
               ("mt_canary_v1", "mt", "report", live.REPORT_BODY),
               ("journal_request_v1", "chatgpt", "task", "JOURNAL_TEST"),
               ("journal_report_v1", "mt", "report", "PRECOMPUTED_TEST")]
    for mid, actor, kind, body in entries:
        head = dialog.append(TASK, head, message_id=mid, actor=actor, kind=kind,
                             body=body, created_at_utc=STAMP).commit_sha
    monkeypatch.setattr(probe, "PREVIOUS_HEAD", head)
    head = dialog.append(TASK, head, message_id=probe.REQUEST_ID, actor="chatgpt",
                         kind="task", body=probe.REQUEST_BODY, created_at_utc=STAMP).commit_sha
    real = api.request
    def request(method, path, payload=None):
        if path == live.BASE + "/git/ref/heads/main":
            return {"ref": "refs/heads/main", "object": {"sha": probe.MAIN_SHA}}
        return real(method, path, payload)
    api.request = request
    api.calls.clear()
    directory = tmp_path / "worker-canary"
    directory.mkdir(mode=0o700)
    return api, head, directory


def run(api, head, directory, action):
    return probe.run_probe(api, directory, head, action)


def writes(api):
    return [c for c in api.calls if c[0] != "GET"]


def test_three_processes_one_computation_one_patch(monkeypatch, tmp_path):
    api, head, directory = setup(monkeypatch, tmp_path)
    calls = []
    evaluate = worker.evaluate
    def counted(*args):
        calls.append(args)
        return evaluate(*args)
    monkeypatch.setattr(worker, "evaluate", counted)
    prepared = run(api, head, directory, "prepare")
    assert prepared["status"] == "received" and prepared["publication_attempted"] == 0
    assert prepared["result_sha256"] is None and prepared["confirmed_commit"] is None
    assert not calls and not writes(api)
    sent = run(api, head, directory, "step")
    assert sent["status"] == "published" and sent["publication_attempted"] == 1
    assert len(calls) == 1 and len([c for c in api.calls if c[0] == "PATCH"]) == 1
    api.calls.clear()
    monkeypatch.setattr(worker, "evaluate", lambda *a: pytest.fail("restart recomputed"))
    observed = run(api, head, directory, "observe")
    assert observed == {**sent, "action": "observe"}
    assert not writes(api)
    assert prepared["input_sha256"] == sent["input_sha256"]
    snapshot = GitHubDialog(api).load(TASK)
    assert json.loads(snapshot.last.body) == {"protocol": "git-dialog-result",
        "version": 1, "op": "ping", "ok": True, "value": "pong"}


def test_step_is_not_repeatable_cli(monkeypatch, tmp_path):
    api, head, directory = setup(monkeypatch, tmp_path)
    run(api, head, directory, "prepare")
    run(api, head, directory, "step")
    api.calls.clear()
    with pytest.raises(DialogError, match="worker_probe_step_already_started"):
        run(api, head, directory, "step")
    assert not writes(api)


def test_existing_prepare_does_not_touch_databases(monkeypatch, tmp_path):
    api, head, directory = setup(monkeypatch, tmp_path)
    run(api, head, directory, "prepare")
    before = [(directory / p).read_bytes() for p in ("journal.sqlite", "lifecycle.sqlite")]
    with pytest.raises(DialogError, match="requires_new_databases"):
        run(api, head, directory, "prepare")
    assert before == [(directory / p).read_bytes() for p in ("journal.sqlite", "lifecycle.sqlite")]
    assert not writes(api)


@pytest.mark.parametrize("action", ["step", "observe"])
def test_missing_databases_not_created(monkeypatch, tmp_path, action):
    api, head, directory = setup(monkeypatch, tmp_path)
    with pytest.raises(DialogError, match="isolated_database_unavailable"):
        run(api, head, directory, action)
    assert not list(directory.iterdir()) and not writes(api)


def test_observe_never_starts_received_task(monkeypatch, tmp_path):
    api, head, directory = setup(monkeypatch, tmp_path)
    run(api, head, directory, "prepare")
    monkeypatch.setattr(worker, "evaluate", lambda *a: pytest.fail("unexpected evaluation"))
    with pytest.raises(DialogError, match="unexpected_worker_probe_result"):
        run(api, head, directory, "observe")
    journal = ExecutionJournal(directory / "journal.sqlite")
    assert journal.get(TASK, probe.REQUEST_ID)["status"] == "received"
    journal.close()
    assert not writes(api)


@pytest.mark.parametrize("action", ["step", "observe"])
def test_started_crash_no_restart_grant(monkeypatch, tmp_path, action):
    api, head, directory = setup(monkeypatch, tmp_path)
    run(api, head, directory, "prepare")
    journal = ExecutionJournal(directory / "journal.sqlite")
    journal.start(TASK, probe.REQUEST_ID, probe.RUN_ID)
    journal.close()
    monkeypatch.setattr(worker, "evaluate", lambda *a: pytest.fail("unexpected evaluation"))
    with pytest.raises(DialogError):
        run(api, head, directory, action)
    assert not writes(api)


def test_lost_ack_reconciles_without_recomputing_or_resending(monkeypatch, tmp_path):
    api, head, directory = setup(monkeypatch, tmp_path)
    run(api, head, directory, "prepare")
    real = api.request
    def lost(method, path, payload=None):
        response = real(method, path, payload)
        if method == "PATCH":
            raise ApiError()
        return response
    api.request = lost
    with pytest.raises(DialogError, match="publication_unknown"):
        run(api, head, directory, "step")
    api.calls.clear()
    monkeypatch.setattr(worker, "evaluate", lambda *a: pytest.fail("unexpected evaluation"))
    assert run(api, head, directory, "observe")["status"] == "published"
    assert not writes(api)


def test_send_failed_before_acceptance_observe_does_not_resend(monkeypatch, tmp_path):
    api, head, directory = setup(monkeypatch, tmp_path)
    run(api, head, directory, "prepare")
    real = api.request
    def failed(method, path, payload=None):
        if method == "PATCH":
            raise ApiError(503)
        return real(method, path, payload)
    api.request = failed
    with pytest.raises(DialogError, match="publication_unknown"):
        run(api, head, directory, "step")
    api.calls.clear()
    with pytest.raises(DialogError, match="publication_unresolved"):
        run(api, head, directory, "observe")
    assert not writes(api)


@pytest.mark.parametrize("case", ["cancelled", "closing", "tombstoned"])
def test_inactive_registry_no_compute_or_patch(monkeypatch, tmp_path, case):
    api, head, directory = setup(monkeypatch, tmp_path)
    run(api, head, directory, "prepare")
    registry = LifecycleRegistry(directory / "lifecycle.sqlite")
    if case == "cancelled":
        registry.cancel(TASK)
    else:
        confirmation = registry.request_delete(TASK, head)
        if case == "tombstoned":
            registry.confirm_delete(TASK, confirmation["confirmation"], head)
    registry.close()
    monkeypatch.setattr(worker, "evaluate", lambda *a: pytest.fail("unexpected evaluation"))
    with pytest.raises(DialogError, match="unexpected_worker_probe_lifecycle"):
        run(api, head, directory, "step")
    assert not writes(api)


def test_wrong_request_parent_no_files_created(monkeypatch, tmp_path):
    api, head, directory = setup(monkeypatch, tmp_path)
    api.commits[head]["parents"] = [{"sha": "f" * 40}]
    with pytest.raises(DialogError, match="unexpected_worker_probe_parent"):
        run(api, head, directory, "prepare")
    assert not list(directory.iterdir()) and not writes(api)


def test_wrong_report_parent_rejected_on_restart(monkeypatch, tmp_path):
    api, head, directory = setup(monkeypatch, tmp_path)
    run(api, head, directory, "prepare")
    run(api, head, directory, "step")
    api.commits[api.refs[REF]]["parents"] = [{"sha": "e" * 40}]
    api.calls.clear()
    with pytest.raises(DialogError, match="unexpected_worker_probe_parent"):
        run(api, head, directory, "observe")
    assert not writes(api)


def test_nonprivate_directory_rejected(monkeypatch, tmp_path):
    api, head, directory = setup(monkeypatch, tmp_path)
    directory.chmod(0o755)
    with pytest.raises(DialogError, match="isolated_directory_required"):
        run(api, head, directory, "prepare")
    assert not list(directory.iterdir()) and not writes(api)


def test_credential_path_not_in_cli_error(capsys):
    assert probe.main(["unknown", "/not/a/credential", "/not/a/database", "a" * 40]) == 1
    out = capsys.readouterr().out
    assert json.loads(out) == {"error": "invalid_worker_probe_action"}
    assert "/not/" not in out


def test_symlink_database_never_followed(monkeypatch, tmp_path):
    api, head, directory = setup(monkeypatch, tmp_path)
    target = tmp_path / "keep"
    target.write_text("unchanged")
    (directory / "journal.sqlite").symlink_to(target)
    with pytest.raises(DialogError):
        run(api, head, directory, "step")
    assert target.read_text() == "unchanged" and not writes(api)


def test_nonmessage_file_mutation_rejected(monkeypatch, tmp_path):
    api, head, directory = setup(monkeypatch, tmp_path)
    tree = dict(api.trees[api.commits[head]["tree"]["sha"]])
    tree["README.md"] = api.entry("changed unexpectedly")
    changed = api.commit(api.tree(tree), [probe.PREVIOUS_HEAD])
    api.refs[REF] = changed
    with pytest.raises(DialogError, match="unexpected_worker_probe_files"):
        run(api, changed, directory, "prepare")
    assert not list(directory.iterdir()) and not writes(api)


def test_missing_remote_ref_never_recreated(monkeypatch, tmp_path):
    api, head, directory = setup(monkeypatch, tmp_path)
    run(api, head, directory, "prepare")
    api.refs.pop(REF)
    with pytest.raises(DialogError, match="branch_missing_or_inaccessible"):
        run(api, head, directory, "step")
    assert not writes(api) and REF not in api.refs
