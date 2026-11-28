import threading

import pytest

from git_dialog import DialogError, runner, runner_runtime


@pytest.mark.parametrize("enabled,expected", [(False, [401, 200, 404]),
                                             (True, [401, 200, 401, 401, 202, 405])])
def test_fixed_cases_and_role_separation(enabled, expected):
    cases = runner_runtime._cases(enabled, "Basic fixture", b"a" * 32)
    assert [case[4] for case in cases] == expected
    for method, path, body, headers, status in cases:
        assert method in ("GET", "POST", "DELETE")
        assert path in ("/api/status", "/bridge/wakeup", "/")
        assert not ({"Authorization", "X-Hermes-Signature"} <= headers.keys())
        if body is not None: assert len(body) <= 4096


@pytest.mark.parametrize("args", [[], ["--self-test", "--port", "8790"],
                                    ["--self-test", "--state-dir", "/unused"],
                                    ["--self-test", "--key", "unused"]])
def test_cli_no_operator_input(args):
    with pytest.raises(SystemExit): runner_runtime.main(args)


def test_cli_safe_failure(monkeypatch, capsys):
    def fail(): raise RuntimeError("never echo credentials")
    monkeypatch.setattr(runner_runtime, "run", fail)
    assert runner_runtime.main(["--self-test"]) == 1
    assert capsys.readouterr().out == '{"error": "runner_runtime_failed"}\n'


def test_main_thread_before_socket():
    errors = []
    def call():
        try: runner.serve_candidate(None, 0, _probe=runner._RuntimeProbe())
        except DialogError as exc: errors.append(exc.code)
    thread = threading.Thread(target=call)
    thread.start(); thread.join(1)
    assert errors == ["candidate_main_thread_required"]


@pytest.mark.parametrize("port,probe", [(0, None), (8787, None), (8789, None),
                                       (0, object()), (65536, None)])
def test_private_hook_does_not_open_arbitrary_ports(port, probe):
    with pytest.raises(DialogError, match="candidate_port_rejected"):
        runner.serve_candidate(None, port, _probe=probe)


def test_cli_success_no_runtime(monkeypatch, capsys):
    monkeypatch.setattr(runner_runtime, "run", lambda: {"status": "passed"})
    assert runner_runtime.main(["--self-test"]) == 0
    assert capsys.readouterr().out == '{"status": "passed"}\n'


def test_inactive_probe_never_signals(monkeypatch):
    monkeypatch.setattr(runner.os, "kill", lambda *a: pytest.fail("inactive signal"))
    runner._RuntimeProbe().terminate_self()


def test_probe_readiness_handler_and_cleanup(tmp_path, monkeypatch):
    from waitress import server as waitress_server
    from test_service import setup
    from git_dialog.wakeup_host import WakeupHost
    _, panel = setup(tmp_path)
    host, probe = WakeupHost(panel), runner._RuntimeProbe()
    handlers, calls = [], []
    monkeypatch.setattr(runner.signal, "signal", lambda sig, handler: handlers.append(handler))
    class Dispatcher:
        threads = set()
        def shutdown(self, **kwargs): calls.append("shutdown")
    class Server:
        effective_port = "19001"
        task_dispatcher = Dispatcher()
        def run(self):
            assert probe.ready.is_set() and probe.signal_active
            assert panel.host == "127.0.0.1:19001"
            assert len(handlers) == 1
            handlers[0]()  # Test handler in-process; no OS signal or socket.
    monkeypatch.setattr(waitress_server, "create_server", lambda *a, **kw: Server())
    runner.serve_candidate(host, 0, _probe=probe)
    assert probe.closed and not probe.signal_active and len(handlers) == 2
    assert calls == ["shutdown"]


def test_non_synthetic_binding_before_socket():
    with pytest.raises(DialogError, match="candidate_synthetic_required"):
        runner.serve_candidate(None, 8790)
