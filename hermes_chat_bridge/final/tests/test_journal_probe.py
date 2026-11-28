import json

import pytest

from git_dialog import DialogError
from git_dialog.github import ApiError, GitHubDialog
from git_dialog.journal import ExecutionJournal
import git_dialog.journal_probe as probe
import git_dialog.live_probe as live
from test_github import FakeApi, TASK, REF, STAMP


def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(probe, "TASK_ID", TASK)
    monkeypatch.setattr(live, "TASK_ID", TASK)
    api = FakeApi()
    dialog = GitHubDialog(api)
    entries = [("chatgpt_canary_v1", "chatgpt", "ack", live.FIRST_BODY),
               ("mt_canary_v1", "mt", "report", live.REPORT_BODY),
               (probe.REQUEST_ID, "chatgpt", "task", probe.REQUEST_BODY)]
    head = api.initial
    for mid, actor, kind, body in entries:
        head = dialog.append(TASK, head, message_id=mid, actor=actor, kind=kind,
                             body=body, created_at_utc=STAMP).commit_sha
    # Include production task mode in the fake task fixture.
    tree = api.trees[api.commits[head]["tree"]["sha"]]
    task = {"protocol": "git-dialog", "version": "1", "task_id": TASK,
            "title": "offline", "mode": "transport_canary_only"}
    tree["task.json"] = api.entry(json.dumps(task))
    real = api.request
    def request(method, path, payload=None):
        if path == probe.BASE + "/git/ref/heads/main":
            return {"ref": "refs/heads/main", "object": {"sha": probe.MAIN_SHA}}
        return real(method, path, payload)
    api.request = request
    api.calls.clear()
    directory = tmp_path / "isolated"
    directory.mkdir(mode=0o700)
    return api, head, directory / "probe.sqlite"


def run(api, head, path, action):
    return probe.run_probe(api, path, head, action)


def writes(api):
    return [c for c in api.calls if c[0] != "GET"]


def test_prepare_send_observe_three_reopens(monkeypatch, tmp_path):
    api, head, path = setup(monkeypatch, tmp_path)
    prepared = run(api, head, path, "prepare")
    assert prepared["status"] == "result_ready" and prepared["publication_attempted"] == 0
    assert not writes(api)
    sent = run(api, head, path, "send")
    assert sent["status"] == "published" and sent["publication_attempted"] == 1
    assert len([c for c in api.calls if c[0] == "PATCH"]) == 1
    assert sent["result_sha256"] == prepared["result_sha256"]
    api.calls.clear()
    observed = run(api, head, path, "observe")
    assert observed == {**sent, "action": "observe"}
    assert not writes(api)


def test_prepare_refuses_existing_database(monkeypatch, tmp_path):
    api, head, path = setup(monkeypatch, tmp_path)
    run(api, head, path, "prepare")
    original = path.read_bytes()
    with pytest.raises(DialogError, match="isolated_database_unavailable"):
        run(api, head, path, "prepare")
    assert path.read_bytes() == original and not writes(api)


def test_observe_before_send_is_read_only(monkeypatch, tmp_path):
    api, head, path = setup(monkeypatch, tmp_path)
    run(api, head, path, "prepare")
    with pytest.raises(DialogError, match="observation_requires_previous_send_attempt"):
        run(api, head, path, "observe")
    assert not writes(api)
    j = ExecutionJournal(path)
    assert j.get(TASK, probe.REQUEST_ID)["publication_attempted"] == 0
    j.close()


def test_accepted_send_lost_ack_then_observe(monkeypatch, tmp_path):
    api, head, path = setup(monkeypatch, tmp_path)
    prepared = run(api, head, path, "prepare")
    real = api.request
    def lost(method, path, payload=None):
        response = real(method, path, payload)
        if method == "PATCH":
            raise ApiError()
        return response
    api.request = lost
    with pytest.raises(DialogError, match="publication_unknown"):
        run(api, head, path, "send")
    api.calls.clear()
    observed = run(api, head, path, "observe")
    assert observed["status"] == "published"
    assert observed["result_sha256"] == prepared["result_sha256"]
    assert not writes(api)


def test_missing_database_send_cannot_create_one(monkeypatch, tmp_path):
    api, head, path = setup(monkeypatch, tmp_path)
    with pytest.raises(DialogError, match="isolated_database_unavailable"):
        run(api, head, path, "send")
    assert not path.exists() and not writes(api)


def test_wrong_initial_head_no_database_creation(monkeypatch, tmp_path):
    api, head, path = setup(monkeypatch, tmp_path)
    with pytest.raises(DialogError, match="unexpected_journal_probe_head"):
        run(api, "a" * 40, path, "prepare")
    assert not path.exists() and not writes(api)


def test_symlink_database_rejected(monkeypatch, tmp_path):
    api, head, path = setup(monkeypatch, tmp_path)
    target = tmp_path / "target"
    target.write_text("do not modify")
    path.symlink_to(target)
    with pytest.raises(DialogError):
        run(api, head, path, "send")
    assert target.read_text() == "do not modify" and not writes(api)


def test_nonprivate_directory_rejected(monkeypatch, tmp_path):
    api, head, path = setup(monkeypatch, tmp_path)
    path.parent.chmod(0o755)
    with pytest.raises(DialogError, match="isolated_directory_required"):
        run(api, head, path, "prepare")
    assert not path.exists() and not writes(api)


def test_unknown_action_no_io(monkeypatch, tmp_path):
    api, head, path = setup(monkeypatch, tmp_path)
    with pytest.raises(DialogError, match="invalid_journal_probe_action"):
        run(api, head, path, "execute")
    assert not api.calls and not path.exists()


def test_observe_refuses_unexpected_report_parent(monkeypatch, tmp_path):
    api, head, path = setup(monkeypatch, tmp_path)
    run(api, head, path, "prepare")
    run(api, head, path, "send")
    api.commits[api.refs[REF]]["parents"] = [{"sha": "f" * 40}]
    api.calls.clear()
    with pytest.raises(DialogError, match="unexpected_journal_probe_parent"):
        run(api, head, path, "observe")
    assert not writes(api)
