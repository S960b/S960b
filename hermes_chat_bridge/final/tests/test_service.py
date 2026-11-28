import base64
import io
import json
import os
import sqlite3
import stat

import pytest

from git_dialog import DialogError
from git_dialog.github import ApiError, GitHubDialog
from git_dialog.service import AuthAuthority, PanelApplication, SnapshotReader, initialize
import git_dialog.service as service
from test_github import FakeApi, TASK, REF, STAMP, writes


PASSWORD = "Test-only-Password-4937"


def auth_header(password=PASSWORD, user="owner"):
    return "Basic " + base64.b64encode((user + ":" + password).encode()).decode()


def setup(tmp_path):
    directory = tmp_path / "sidecar"
    initialize(directory, PASSWORD)
    return directory, PanelApplication(AuthAuthority(directory), SnapshotReader())


def call(app, path="/", auth=None, **extra):
    env = {"REQUEST_METHOD": "GET", "PATH_INFO": path, "QUERY_STRING": "",
           "HTTP_HOST": "127.0.0.1:8789", "REMOTE_ADDR": "127.0.0.1", "wsgi.input": io.BytesIO()}
    if auth is not None: env["HTTP_AUTHORIZATION"] = auth
    env.update(extra)
    received = []
    body = b"".join(app(env, lambda status, headers: received.append((status, dict(headers)))))
    return int(received[0][0].split()[0]), received[0][1], body


def test_initialize_new_private_database_only(tmp_path):
    directory, app = setup(tmp_path)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / "panel.sqlite").stat().st_mode) == 0o600
    assert PASSWORD.encode() not in (directory / "panel.sqlite").read_bytes()
    with pytest.raises(DialogError, match="service_state_already_exists"): initialize(directory, PASSWORD)


@pytest.mark.parametrize("password", ["short", "x" * 257, "a" * 14 + "\n", "\ud800" * 14])
def test_password_validation_before_files(tmp_path, password):
    directory = tmp_path / "new"
    with pytest.raises(DialogError, match="password_rejected"): initialize(directory, password)
    assert not directory.exists()


def test_authentication_required_for_html_and_bridge_status(tmp_path):
    directory, app = setup(tmp_path)
    for path in ("/", "/history", "/api/status", "/bridge/status"):
        status, headers, body = call(app, path)
        assert status == 401 and headers["WWW-Authenticate"].startswith("Basic ")
        assert b"FIXTURE" not in body and headers["Cache-Control"] == "no-store"


def test_authenticated_synthetic_html_and_bridge_contract(tmp_path):
    directory, app = setup(tmp_path)
    status, headers, body = call(app, auth=auth_header())
    assert status == 200 and b"FIXTURE_PONG_NO_EXECUTION" in body
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Refresh"] == "15"
    status, headers, body = call(app, "/bridge/status", auth=auth_header())
    payload = json.loads(body)
    assert status == 200 and payload["mode"] == "synthetic"
    assert payload["read_only"] and not payload["delete_enabled"]
    assert "passive_sidecar" in payload["bridge"]


def test_wrong_password_rate_limit_and_no_secret_echo(tmp_path):
    directory, app = setup(tmp_path)
    for _ in range(5): assert call(app, auth=auth_header("Incorrect-Password-4937"))[0] == 401
    code, headers, body = call(app, auth=auth_header())
    assert code == 429 and headers["Retry-After"] == "60" and PASSWORD.encode() not in body
    app.authority._window -= 61
    assert call(app, auth=auth_header())[0] == 200


@pytest.mark.parametrize("value", ["Bearer wrong", "Basic !!!", "Basic " + "x" * 1100, auth_header(user="öwner")])
def test_bad_authorization_fail_closed(tmp_path, value):
    directory, app = setup(tmp_path)
    assert call(app, auth=value)[0] == 401


@pytest.mark.parametrize("change", [{"HTTP_HOST": "localhost:8789"}, {"HTTP_HOST": "127.0.0.1:8787"},
    {"REMOTE_ADDR": "8.8.8.8"}, {"HTTP_ORIGIN": "http://evil.example"}, {"HTTP_X_FORWARDED_HOST": "evil"}])
def test_host_origin_proxy_and_peer_denied(tmp_path, change):
    directory, app = setup(tmp_path)
    assert call(app, auth=auth_header(), **change)[0] == 403


@pytest.mark.parametrize("method", ["POST", "DELETE", "PUT", "PATCH"])
def test_all_mutations_disabled(tmp_path, method):
    directory, app = setup(tmp_path)
    assert call(app, "/bridge/status", auth=auth_header(), REQUEST_METHOD=method)[0] == 405


def fake_poll(monkeypatch):
    monkeypatch.setattr(service, "TASK_ID", TASK)
    api = FakeApi(); original = api.request
    def request(method, path, payload=None):
        if path.endswith("/git/ref/heads/main"):
            return {"ref": "refs/heads/main", "object": {"sha": "38ed706d21a0ca7520271e561e4406f777601261"}}
        return original(method, path, payload)
    api.request = request
    return api


def test_real_reader_offline_double_only_gets_and_tracks_append(monkeypatch):
    api = fake_poll(monkeypatch)
    reader = SnapshotReader(api); reader.refresh()
    assert reader.current().head == api.initial and not writes(api)
    receipt = GitHubDialog(api).append(TASK, api.initial, message_id="m1", actor="chatgpt",
        kind="task", body="data not execution", created_at_utc=STAMP)
    api.calls.clear(); reader.refresh()
    assert reader.current().head == receipt.commit_sha and not writes(api)
    api.calls.clear(); reader.refresh()
    assert len(api.calls) < 10 and not writes(api)  # Unchanged HEAD skips history reload.
    with pytest.raises(DialogError): reader.transport.request("PATCH", "/repos/S960b/agent-dialogs/git/refs/heads/main", {})


@pytest.mark.parametrize("failure", ["missing", "403", "main", "rewind"])
def test_poll_failure_no_stale_html_or_synthetic_fallback(monkeypatch, tmp_path, failure):
    api = fake_poll(monkeypatch)
    GitHubDialog(api).append(TASK, api.initial, message_id="m1", actor="chatgpt", kind="task", body="data", created_at_utc=STAMP)
    reader = SnapshotReader(api); reader.refresh(); assert reader.current()
    if failure == "missing": api.refs.clear()
    elif failure == "rewind": api.refs[REF] = api.initial
    else:
        original = api.request
        def fail(method, path, payload=None):
            if failure == "403": raise ApiError(403)
            if path.endswith("/git/ref/heads/main"): return {"ref": "refs/heads/main", "object": {"sha": "a" * 40}}
            return original(method, path, payload)
        api.request = fail
    api.calls.clear(); reader.refresh()
    with pytest.raises(DialogError, match="source_unavailable"): reader.current()
    assert reader.status()["error"] and reader.mode == "github" and not writes(api)
    directory, app = setup(tmp_path); app.reader = reader
    assert call(app, auth=auth_header())[0] == 503
    assert call(app, "/api/status", auth=auth_header())[0] == 200


def test_foreign_and_symlink_state_rejected(tmp_path):
    directory, app = setup(tmp_path)
    link = tmp_path / "link"; link.symlink_to(directory)
    with pytest.raises(DialogError): AuthAuthority(link)
    directory.chmod(0o755)
    with pytest.raises(DialogError): AuthAuthority(directory)


@pytest.mark.parametrize("port", [8787, 0, 80, 65536])
def test_protected_ports_rejected(tmp_path, port):
    directory, app = setup(tmp_path)
    with pytest.raises(DialogError, match="service_port_rejected"): PanelApplication(app.authority, app.reader, port)


def test_cli_serve_is_synthetic_by_default_and_locked(tmp_path, monkeypatch):
    import waitress
    directory, app = setup(tmp_path)
    called = []
    def serve(application, **kwargs):
        called.append(kwargs)
        assert application.reader.mode == "synthetic" and kwargs["host"] == "127.0.0.1"
        assert service.main(["serve", "--state-dir", str(directory)]) == 1  # Same state cannot start twice.
    monkeypatch.setattr(waitress, "serve", serve)
    assert service.main(["serve", "--state-dir", str(directory)]) == 0
    assert len(called) == 1 and called[0]["port"] == 8789 and not called[0]["expose_tracebacks"]
