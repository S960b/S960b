import pytest
from git_dialog import runner
from git_dialog.service import initialize


@pytest.mark.parametrize("port", [0, 80, 8787, 8789, 65536])
def test_protected_ports_no_state(tmp_path, port):
    path = tmp_path / "new"
    assert runner.main(["init", "--state-dir", str(path), "--port", str(port)]) == 1
    assert not path.exists()


def test_old_p1_state_not_reused(tmp_path):
    path = tmp_path / "old"
    initialize(path, "fixture-password-1234")
    before = (path / "panel.sqlite").read_bytes()
    assert runner.main(["serve", "--state-dir", str(path)]) == 1
    assert (path / "panel.sqlite").read_bytes() == before
    assert not (path / "server.lock").exists()


def test_new_init_serve_default_synthetic_off(tmp_path, monkeypatch):
    path = tmp_path / "new"
    monkeypatch.setattr(runner, "_interactive_secret", lambda _: "fixture-password-1234")
    assert runner.main(["init", "--state-dir", str(path)]) == 0
    calls = []
    def serve(host, port):
        calls.append(port)
        assert host._application.gate is None
        assert host.loop._reader.mode == "synthetic"
        assert runner.main(["serve", "--state-dir", str(path)]) == 1
    monkeypatch.setattr(runner, "serve_candidate", serve)
    assert runner.main(["serve", "--state-dir", str(path)]) == 0
    assert calls == [8790]
    assert runner.main(["init", "--state-dir", str(path)]) == 1


def test_key_only_explicit_provision_no_echo(tmp_path, monkeypatch, capsys):
    path = tmp_path / "new"
    monkeypatch.setattr(runner, "_interactive_secret", lambda _: "fixture-password-1234")
    assert runner.main(["init", "--state-dir", str(path)]) == 0
    assert runner.main(["key-init", "--state-dir", str(path)]) == 0
    key = (path / "wakeup.key").read_bytes()
    monkeypatch.setattr(runner, "_interactive_secret", lambda _: pytest.fail("no key prompt"))
    monkeypatch.setattr(runner, "serve_candidate", lambda host, port: None)
    assert runner.main(["serve", "--state-dir", str(path), "--enable-wakeup"]) == 0
    output = capsys.readouterr().out
    assert key.hex() not in output and "wakeup.key" not in output


def test_no_github_or_key_cli(tmp_path):
    with pytest.raises(SystemExit): runner.main(["serve", "--state-dir", str(tmp_path), "--key", "secret"])


def test_server_wiring_cleanup_no_sockets(tmp_path, monkeypatch):
    import threading
    from waitress import server as waitress_server
    from test_service import setup
    from git_dialog.wakeup_host import WakeupHost
    _, panel = setup(tmp_path); refreshed = threading.Event(); calls = []
    monkeypatch.setattr(panel.reader, "refresh", refreshed.set)
    class Dispatcher:
        threads = set()
        def shutdown(self, **kwargs): calls.append("shutdown")
    class Server:
        task_dispatcher = Dispatcher()
        def run(self): assert refreshed.wait(timeout=2)
    def create(app, **kwargs):
        assert kwargs["host"] == "127.0.0.1" and kwargs["port"] == 8790
        assert not kwargs["expose_tracebacks"]
        return Server()
    monkeypatch.setattr(waitress_server, "create_server", create)
    runner.serve_candidate(WakeupHost(panel), 8790)
    assert calls == ["shutdown"]
