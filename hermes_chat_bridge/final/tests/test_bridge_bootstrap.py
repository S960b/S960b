import hashlib
from types import SimpleNamespace

import pytest

from git_dialog import bridge_bootstrap as boot


def files(tmp_path, monkeypatch):
    original = tmp_path / 'bridge_server.py'
    candidate = tmp_path / 'candidate.py'
    raw = b'raise RuntimeError("must not execute during check")\n'
    for path in (original, candidate): path.touch(mode=0o600); path.write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(boot, 'CONTEXT_SHA', sha)
    monkeypatch.setattr(boot, 'CANDIDATE_SHA', sha)
    return ['--source', str(candidate), '--context-source', str(original)]


def test_check_does_not_import_or_execute(tmp_path, monkeypatch, capsys):
    args = files(tmp_path, monkeypatch)
    monkeypatch.setattr(boot, '_execute', lambda *a: pytest.fail('execution'))
    assert boot.main(args) == 0
    assert '"bridge_started": false' in capsys.readouterr().out


def test_hash_change_rejected(tmp_path, monkeypatch):
    args = files(tmp_path, monkeypatch)
    (tmp_path / 'candidate.py').write_text('pass\n')
    assert boot.main(args) == 1


@pytest.mark.parametrize('port', [0, 80, 8787, 8789, 65536])
def test_ports_before_read(tmp_path, port):
    assert boot.main(['--source', 'missing', '--context-source', 'missing', '--port', str(port)]) == 1


def test_run_without_state_never_executes(tmp_path, monkeypatch):
    args = files(tmp_path, monkeypatch)
    monkeypatch.setattr(boot, '_execute', lambda *a: pytest.fail('execution'))
    assert boot.main(args + ['--run']) == 1


def test_check_rejects_state_argument(tmp_path, monkeypatch):
    assert boot.main(files(tmp_path, monkeypatch) + ['--state-dir', str(tmp_path)]) == 1


def test_symlink_and_writable_source_rejected(tmp_path, monkeypatch):
    args = files(tmp_path, monkeypatch)
    (tmp_path / 'candidate.py').chmod(0o666)
    assert boot.main(args) == 1
    (tmp_path / 'candidate.py').chmod(0o600)
    link = tmp_path / 'link'; link.symlink_to(tmp_path / 'candidate.py')
    args[1] = str(link)
    assert boot.main(args) == 1


def test_configure_before_lifespan_and_preserve_arguments():
    seen = []
    app = SimpleNamespace(state=SimpleNamespace(bridge=SimpleNamespace(
        enable_beta_wakeup=lambda state, **kw: seen.append((state, kw))),
        pilot_lock=SimpleNamespace(close=lambda: seen.append('close'))))
    def build(base, **kw): seen.append((base, kw)); return app
    module = SimpleNamespace(build_app=build)
    assert boot._configure(module, 'fixture-state', 8790) is build
    assert module.build_app('fixture-base', option=True) is app
    assert seen == [('fixture-base', {'option': True}), ('fixture-state', {'port': 8790})]


def test_enable_failure_releases_original_pilot_lock():
    seen = []
    def fail(*a, **kw): raise RuntimeError('fixture')
    app = SimpleNamespace(state=SimpleNamespace(bridge=SimpleNamespace(enable_beta_wakeup=fail),
        pilot_lock=SimpleNamespace(close=lambda: seen.append('close'))))
    module = SimpleNamespace(build_app=lambda *a: app)
    boot._configure(module, 'fixture', 8790)
    with pytest.raises(RuntimeError): module.build_app('base')
    assert seen == ['close']


def test_execute_trusted_fixture_preserves_context_and_enables_before_main(tmp_path):
    import sys
    raw = b'''from types import SimpleNamespace
import sys
class Bridge:
    enabled = False
    def enable_beta_wakeup(self, state_dir, *, port):
        assert state_dir == 'fixture-state' and port == 8790
        self.enabled = True
def build_app(base):
    assert base == 'fixture-base'
    return SimpleNamespace(state=SimpleNamespace(bridge=Bridge()))
def main():
    app = build_app('fixture-base')
    assert app.state.bridge.enabled
    assert __file__ == sys.argv[0]
    assert __name__ != '__main__'
    return 0
'''
    previous_path, previous_argv = list(sys.path), list(sys.argv)
    assert boot._execute(raw, tmp_path / 'bridge_server.py', 'fixture-state', 8790) == 0
    assert sys.path == previous_path and sys.argv == previous_argv
    assert '_git_dialog_pinned_beta_bridge' not in sys.modules


def state(tmp_path):
    from git_dialog.service import initialize
    from git_dialog.wakeup_key import provision
    directory = tmp_path / 'state'
    initialize(directory, 'fixture-only-password-123')
    (directory / 'runner-candidate.marker').touch(mode=0o600)
    provision(directory)
    return directory


def test_explicit_validation_checks_real_temporary_state_without_execution(tmp_path, monkeypatch, capsys):
    import json
    args = files(tmp_path, monkeypatch)
    directory = state(tmp_path)
    before = (directory / 'panel.sqlite').read_bytes(), (directory / 'wakeup.key').read_bytes()
    monkeypatch.setattr(boot, '_execute', lambda *a: pytest.fail('bridge execution'))
    assert boot.main(args + ['--validate-state', '--state-dir', str(directory)]) == 0
    assert json.loads(capsys.readouterr().out) == {'status': 'prerequisites_checked',
        'state_validated': True, 'key_validated': True, 'bridge_started': False}
    assert before == ((directory / 'panel.sqlite').read_bytes(), (directory / 'wakeup.key').read_bytes())


def test_validation_missing_state_is_fixed_error(tmp_path, monkeypatch, capsys):
    import json
    assert boot.main(files(tmp_path, monkeypatch) + ['--validate-state']) == 1
    assert json.loads(capsys.readouterr().out) == {'error': 'bootstrap_failed', 'phase': 'options',
        'type': 'DialogError', 'code': 'bootstrap_state_required'}


@pytest.mark.parametrize('target,phase', [('state', 'state_validation'), ('key', 'key_validation')])
def test_validation_failure_reports_fixed_phase_without_secret(tmp_path, monkeypatch, capsys, target, phase):
    import json
    from git_dialog import service, wakeup_key
    args = files(tmp_path, monkeypatch)
    def fail(*a): raise PermissionError('private-token-and-private-path')
    if target == 'state': monkeypatch.setattr(service, 'AuthAuthority', fail)
    else:
        monkeypatch.setattr(service, 'AuthAuthority', lambda *a: None)
        monkeypatch.setattr(wakeup_key, 'load', fail)
    assert boot.main(args + ['--validate-state', '--state-dir', 'not-a-credential']) == 1
    output = capsys.readouterr().out
    assert 'private-token' not in output and 'private-path' not in output
    assert json.loads(output) == {'error': 'bootstrap_failed', 'phase': phase,
                                  'type': 'PermissionError', 'code': 'unclassified'}


@pytest.mark.parametrize('raw,phase,kind', [
    (b'raise ImportError("private detail")\n', 'module_execute', 'ImportError'),
    (b'def main(): pass\n', 'configure', 'AttributeError'),
    (b'def build_app(): pass\ndef main(): raise TypeError("private detail")\n', 'main', 'TypeError'),
])
def test_execution_failure_phase_is_sanitized_and_context_restored(tmp_path, raw, phase, kind):
    import sys
    before = list(sys.path), list(sys.argv)
    with pytest.raises(boot.BoundaryFailure) as caught:
        boot._execute(raw, tmp_path / 'bridge_server.py', 'fixture', 8790)
    assert boot._failure('options', caught.value) == {'error': 'bootstrap_failed',
        'phase': phase, 'type': kind, 'code': 'unclassified'}
    assert (sys.path, sys.argv) == before
    assert '_git_dialog_pinned_beta_bridge' not in sys.modules


def test_build_and_enable_phases_are_distinct():
    def fail(*a, **kw): raise ValueError('private detail')
    broken = SimpleNamespace(build_app=fail)
    boot._configure(broken, 'fixture', 8790)
    with pytest.raises(boot.BoundaryFailure) as caught: broken.build_app()
    assert caught.value.phase == 'build_app'
    seen = []
    app = SimpleNamespace(state=SimpleNamespace(bridge=SimpleNamespace(enable_beta_wakeup=fail),
        pilot_lock=SimpleNamespace(close=lambda: seen.append('closed'))))
    module = SimpleNamespace(build_app=lambda: app)
    boot._configure(module, 'fixture', 8790)
    with pytest.raises(boot.BoundaryFailure) as caught: module.build_app()
    assert caught.value.phase == 'enable_wakeup' and seen == ['closed']


def test_unknown_exception_class_code_and_phase_do_not_escape():
    from git_dialog import DialogError
    private = type('PrivateCredentialValue', (Exception,), {})('private detail')
    assert boot._failure('private path', private) == {'error': 'bootstrap_failed',
        'phase': 'options', 'type': 'Exception', 'code': 'unclassified'}
    assert boot._failure('main', DialogError('private-code', 'private detail'))['code'] == 'unclassified'


def test_enable_cleanup_failure_is_sanitized():
    def fail(*a, **kw): raise PermissionError('private detail')
    app = SimpleNamespace(state=SimpleNamespace(bridge=SimpleNamespace(enable_beta_wakeup=fail),
        pilot_lock=SimpleNamespace(close=fail)))
    module = SimpleNamespace(build_app=lambda: app)
    boot._configure(module, 'fixture', 8790)
    with pytest.raises(boot.BoundaryFailure) as caught: module.build_app()
    assert boot._failure('main', caught.value) == {'error': 'bootstrap_failed',
        'phase': 'enable_cleanup', 'type': 'PermissionError', 'code': 'unclassified'}
