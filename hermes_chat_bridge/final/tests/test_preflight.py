import json

import pytest

from git_dialog import DialogError
from git_dialog.github import ApiError
from git_dialog.preflight import (BASE, MAIN_SHA, ReadOnlyTransport, main,
                                  read_configured_token, run_preflight)


class ReadApi:
    def __init__(self):
        self.calls = []
        self.private = True
        self.main_sha = MAIN_SHA

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        assert method == "GET" and payload is None
        if path == BASE:
            return {"private": self.private, "full_name": "S960b/agent-dialogs"}
        if path == BASE + "/git/ref/heads/main":
            return {"ref": "refs/heads/main", "object": {"type": "commit", "sha": self.main_sha}}
        if "/git/ref/heads/dialogs/" in path:
            raise ApiError(404)
        raise AssertionError("unexpected request")


def test_missing_branch_read_only_live_contract():
    api = ReadApi()
    result = run_preflight(api)
    assert result["read_transport"] == "passed"
    assert result["write_transport"] == "not_tested"
    assert result["publication"] == "blocked_requires_explicit_branch_creation"
    assert result["mutations"] == 0
    assert len(api.calls) == result["requests"] == 5


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE", "PUT"])
def test_guard_blocks_mutation_before_transport(method):
    api = ReadApi()
    with pytest.raises(DialogError, match="preflight_write_forbidden"):
        ReadOnlyTransport(api).request(method, BASE)
    assert api.calls == []


def test_guard_rejects_other_repository():
    api = ReadApi()
    with pytest.raises(DialogError, match="preflight_scope_forbidden"):
        ReadOnlyTransport(api).request("GET", "/repos/S960b/other")
    assert not api.calls


@pytest.mark.parametrize("case", ["public", "main"])
def test_checkpoint_refusals(case):
    api = ReadApi()
    if case == "public":
        api.private = False
    else:
        api.main_sha = "a" * 40
    with pytest.raises(DialogError):
        run_preflight(api)


def test_only_explicit_private_regular_credential_file(tmp_path):
    path = tmp_path / "nonsecret-test-credential"
    path.write_text("nonsecret-sentinel\n")
    path.chmod(0o600)
    assert read_configured_token(path) == "nonsecret-sentinel"
    for mode in (0o644, 0o400, 0o666):
        path.chmod(mode)
        with pytest.raises(DialogError, match="credential_file_rejected"):
            read_configured_token(path)
    path.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(DialogError, match="credential_file_rejected"):
        read_configured_token(link)


@pytest.mark.parametrize("content", ["", "bad token", "bad\x00token", "x" * 4097])
def test_invalid_local_credentials_rejected(tmp_path, content):
    path = tmp_path / "test-value"
    path.write_text(content)
    path.chmod(0o600)
    with pytest.raises(DialogError, match="credential_file_rejected"):
        read_configured_token(path)


def test_main_redacts_local_path_and_errors(tmp_path, capsys, monkeypatch):
    import git_dialog.preflight as module
    path = tmp_path / "do-not-echo-this-path"
    assert main([str(path)]) == 1
    assert json.loads(capsys.readouterr().out) == {"error": "credential_file_rejected"}
    monkeypatch.setattr(module, "read_configured_token", lambda _: "nonsecret-sentinel")
    def fail(*args):
        raise RuntimeError("must-not-echo-secret-or-path")
    monkeypatch.setattr(module, "JsonHttp", fail)
    assert main([str(path)]) == 1
    assert json.loads(capsys.readouterr().out) == {"error": "preflight_failed"}


def test_main_live_read_success_with_injected_http(capsys, monkeypatch):
    import git_dialog.preflight as module
    monkeypatch.setattr(module, "read_configured_token", lambda _: "nonsecret-sentinel")
    monkeypatch.setattr(module, "JsonHttp", lambda _: ReadApi())
    assert main(["explicit-config-not-a-real-path"]) == 0
    assert json.loads(capsys.readouterr().out)["mutations"] == 0


def test_existing_snapshot_is_read_not_published(monkeypatch):
    import git_dialog.preflight as module
    from test_github import FakeApi, TASK
    api = FakeApi()
    real = api.request
    def request(method, path, payload=None):
        if path == BASE + "/git/ref/heads/main":
            return {"ref": "refs/heads/main", "object": {"type": "commit", "sha": MAIN_SHA}}
        return real(method, path, payload)
    api.request = request
    monkeypatch.setattr(module, "TASK_ID", TASK)
    result = module.run_preflight(api)
    assert result["dialog"] == "valid_snapshot"
    assert result["seq"] == 0 and result["publication"] == "not_attempted"
    assert all(method == "GET" for method, _, _ in api.calls)


@pytest.mark.parametrize("status", [401, 429])
def test_main_sanitizes_http_status(capsys, monkeypatch, status):
    import git_dialog.preflight as module
    monkeypatch.setattr(module, "read_configured_token", lambda _: "nonsecret-sentinel")
    class Failed:
        def request(self, *args):
            raise ApiError(status)
    monkeypatch.setattr(module, "JsonHttp", lambda _: Failed())
    assert main(["local-config"]) == 1
    assert json.loads(capsys.readouterr().out) == {"error": "github_http_error", "status": status}
