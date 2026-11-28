import pytest
from git_dialog import DialogError
from git_dialog.github import ApiError, GitHubDialog
import git_dialog.live_probe as probe
from test_github import FakeApi, TASK, REF


def setup(monkeypatch):
    api = FakeApi()
    monkeypatch.setattr(probe, "TASK_ID", TASK)
    receipt = GitHubDialog(api).append(TASK, api.initial, message_id=probe.FIRST_ID,
                                      actor="chatgpt", kind="ack", body=probe.FIRST_BODY)
    real = api.request
    def request(method, path, payload=None):
        if path == probe.BASE + "/git/ref/heads/main":
            return {"ref": "refs/heads/main", "object": {"sha": probe.MAIN_SHA}}
        return real(method, path, payload)
    api.request = request
    api.calls.clear()
    return api, receipt.commit_sha


def test_one_report_then_repeat_is_read_only(monkeypatch):
    api, head = setup(monkeypatch)
    result = probe.run_probe(api, head)
    assert result["created"] and result["seq"] == 2
    assert result["next_actor"] == "chatgpt"
    assert len([c for c in api.calls if c[0] == "PATCH"]) == 1
    api.calls.clear()
    repeated = probe.run_probe(api, head)
    assert not repeated["created"]
    assert all(c[0] == "GET" for c in api.calls)


def test_missing_branch_cannot_be_created(monkeypatch):
    api, head = setup(monkeypatch)
    api.refs.clear()
    with pytest.raises(DialogError, match="branch_missing_or_inaccessible"):
        probe.run_probe(api, head)
    assert all(c[0] == "GET" for c in api.calls)


def test_wrong_initial_head_no_writes(monkeypatch):
    api, head = setup(monkeypatch)
    with pytest.raises(DialogError, match="unexpected_initial_head"):
        probe.run_probe(api, "a" * 40)
    assert all(c[0] == "GET" for c in api.calls)


@pytest.mark.parametrize("method,path,payload", [
    ("POST", "/git/refs", {"ref": "refs/heads/new"}),
    ("DELETE", "/git/refs/heads/dialogs/" + TASK, None),
    ("PATCH", "/git/refs/heads/main", {"sha": "a" * 40, "force": False}),
    ("PATCH", "/git/refs/heads/dialogs/" + TASK, {"sha": "a" * 40, "force": True}),
])
def test_guard_rejects_other_mutations(monkeypatch, method, path, payload):
    api, head = setup(monkeypatch)
    with pytest.raises(DialogError, match="live_probe_request_forbidden"):
        probe.ProbeTransport(api).request(method, probe.BASE + path, payload)
    assert not api.calls


def test_lost_ack_does_not_retry_patch(monkeypatch):
    api, head = setup(monkeypatch)
    real = api.request
    def lost(method, path, payload=None):
        result = real(method, path, payload)
        if method == "PATCH":
            raise ApiError()
        return result
    api.request = lost
    with pytest.raises(DialogError, match="publication_unknown"):
        probe.run_probe(api, head)
    assert len([c for c in api.calls if c[0] == "PATCH"]) == 1
    assert len(GitHubDialog(api).load(TASK).messages) == 2
