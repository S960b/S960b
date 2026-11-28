import io
import json
import subprocess
import threading

import pytest

from git_dialog import DialogError
from git_dialog import isolated_read as mod
from git_dialog.service import SnapshotReader
from git_dialog.wakeup_host import SerialReaderLoop


class Process:
    def __init__(self, output=b'', returncode=0):
        self.output, self.returncode = output, returncode
        self.stdin, self.stdout = io.BytesIO(), io.BytesIO()
        self.terminated = self.killed = False
    def communicate(self, **kw): return self.output, None
    def poll(self): return self.returncode
    def terminate(self): self.terminated = True; self.returncode = -15
    def kill(self): self.killed = True; self.returncode = -9
    def wait(self, **kw): return self.returncode


def bind(monkeypatch, process):
    def popen(argv, **kwargs):
        assert argv == [mod.sys.executable, '-m', 'git_dialog.isolated_read']
        assert 'fixture-secret' not in str(argv) + str(kwargs)
        assert set(kwargs['env']) == {'PYTHONPATH', 'LANG'}
        assert kwargs['stderr'] == subprocess.DEVNULL and kwargs['close_fds']
        return process
    monkeypatch.setattr(mod.subprocess, 'Popen', popen)


@pytest.mark.parametrize('budget', [0, -1, 31, float('nan'), True, '15'])
def test_invalid_budget(budget):
    with pytest.raises(DialogError): mod.IsolatedRead('fixture-secret', budget=budget)


@pytest.mark.parametrize('token', ['', 'hello\nworld', None, 'é'])
def test_invalid_secret(token):
    with pytest.raises(DialogError): mod.IsolatedRead(token)


@pytest.mark.parametrize('output', [b'{}', b'null', b'invalid', b'{"snapshot":null,"error":null}',
                                  b'{"snapshot":null,"error":"SECRET"}'])
def test_bad_output_safe_and_closed(monkeypatch, output):
    process = Process(output)
    bind(monkeypatch, process)
    with pytest.raises(DialogError) as error: mod.IsolatedRead('fixture-secret').read(None)
    assert 'SECRET' not in str(error.value)
    assert process.stdin.closed and process.stdout.closed


def test_cancel_before_spawn():
    reader = mod.IsolatedRead('fixture-secret'); reader.cancel()
    with pytest.raises(DialogError, match='poll_cancelled'): reader.read(None)


def test_deadline_disposes_only_owned_child(monkeypatch):
    process = Process(returncode=None)
    def timeout(**kw): raise subprocess.TimeoutExpired('fixed', kw['timeout'])
    process.communicate = timeout
    bind(monkeypatch, process)
    clock = iter([0, 0, 2])
    monkeypatch.setattr(mod.time, 'monotonic', lambda: next(clock))
    with pytest.raises(DialogError, match='poll_deadline'):
        mod.IsolatedRead('fixture-secret', budget=1).read(None)
    assert process.terminated and process.stdin.closed


def test_cancel_inflight_disposes(monkeypatch):
    reader = mod.IsolatedRead('fixture-secret')
    process = Process(returncode=None)
    def timeout(**kw):
        reader.cancel()
        raise subprocess.TimeoutExpired('fixed', kw['timeout'])
    process.communicate = timeout; bind(monkeypatch, process)
    with pytest.raises(DialogError, match='poll_cancelled'): reader.read(None)
    assert process.terminated


def test_escalate_only_owned_child(monkeypatch):
    process = Process(returncode=None)
    process.terminate = lambda: None
    def wait(**kw):
        if not process.killed: raise subprocess.TimeoutExpired('fixed', 0.3)
    process.wait = wait
    mod._dispose(process)
    assert process.killed and process.stdin.closed


def test_success_and_snapshot_parent_validation(monkeypatch):
    from git_dialog.panel import canary_snapshot
    snap = canary_snapshot()
    monkeypatch.setattr(mod, 'TASK_ID', snap.state.task_id)
    # Synthetic fixture head is not a Git hash; supply one for the IPC contract.
    snap.head = 'a' * 40
    process = Process(json.dumps({'snapshot': mod.pack(snap), 'error': None}).encode())
    bind(monkeypatch, process)
    assert mod.IsolatedRead('fixture-secret').read(None) == snap


def test_reader_errors_fail_closed(monkeypatch):
    isolated = mod.IsolatedRead('fixture-secret')
    monkeypatch.setattr(isolated, 'read', lambda previous: (_ for _ in ()).throw(DialogError('poll_deadline')))
    reader = SnapshotReader(isolated=isolated); reader.refresh()
    assert reader.status()['mode'] == 'github' and reader.status()['error'] == 'poll_deadline'
    with pytest.raises(DialogError, match='source_unavailable'): reader.current()


def test_stop_cancels_before_waiting_refresh_lock(monkeypatch):
    isolated = mod.IsolatedRead('fixture-secret')
    entered = threading.Event()
    def read(previous):
        entered.set()
        assert isolated._stop.wait(1)
        raise DialogError('poll_cancelled')
    monkeypatch.setattr(isolated, 'read', read)
    reader = SnapshotReader(isolated=isolated); loop = SerialReaderLoop(reader)
    thread = threading.Thread(target=loop.tick)
    thread.start(); assert entered.wait(1)
    loop.stop(); thread.join(1)
    assert not thread.is_alive() and loop.tick()['reason'] == 'stopped'


def test_binding_rejected():
    with pytest.raises(DialogError): SnapshotReader(isolated=object())


def test_child_malformed_input_no_network(monkeypatch):
    class Input: buffer = io.BytesIO(b'{}')
    out = io.StringIO()
    monkeypatch.setattr(mod.sys, 'stdin', Input())
    monkeypatch.setattr(mod.sys, 'stdout', out)
    assert mod.child() == 1
    assert json.loads(out.getvalue())['snapshot'] is None


def test_git_cli_explicit_only_new_state(tmp_path, monkeypatch, capsys):
    from git_dialog import runner, preflight
    path = tmp_path / 'beta'
    monkeypatch.setattr(runner, '_interactive_secret', lambda _: 'fixture-password-1234')
    assert runner.main(['init', '--state-dir', str(path), '--mode', 'github']) == 1
    assert not path.exists()
    assert runner.main(['init', '--state-dir', str(path)]) == 0
    monkeypatch.setattr(runner, '_interactive_secret', lambda _: 'explicit-fixture-path')
    def token(path):
        assert path == 'explicit-fixture-path'
        return 'fixture-secret'
    monkeypatch.setattr(preflight, 'read_configured_token', token)
    def serve(host, port):
        assert host.loop._reader.mode == 'github'
        assert type(host.loop._reader._isolated) is mod.IsolatedRead
        assert host._application.gate is None
    monkeypatch.setattr(runner, 'serve_candidate', serve)
    assert runner.main(['serve', '--state-dir', str(path), '--mode', 'github']) == 0
    output = capsys.readouterr().out
    assert 'fixture-secret' not in output and 'explicit-fixture-path' not in output


@pytest.mark.parametrize('args', [[], ['--self-test', '--token', 'fixture']])
def test_process_smoke_rejects_arguments(args):
    from git_dialog.isolated_read_smoke import main
    with pytest.raises(SystemExit): main(args)
