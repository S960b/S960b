import importlib.util
from pathlib import Path
import sys

import pytest

from git_dialog import bridge_bootstrap


ROOT = Path(__file__).absolute().parent.parent


def launcher():
    spec = importlib.util.spec_from_file_location('fixture_launcher', ROOT / 'deploy/hermes-beta-launcher.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_only_pinned_check(monkeypatch):
    seen = []
    monkeypatch.setattr(bridge_bootstrap, 'main', lambda args: seen.append(args) or 0)
    previous = list(sys.path)
    assert launcher().main([]) == 0
    assert seen == [['--source', '/home/kali/agent-dialog-beta/bridge-candidate.py',
                     '--context-source', '/home/kali/S960b/research/hermes_chat_bridge/bridge_server.py']]
    assert sys.path == previous


def test_run_is_explicit_fixed_state_port_and_preserves_context(monkeypatch):
    import os
    seen = []
    previous_path, previous_argv, previous_env = list(sys.path), list(sys.argv), dict(os.environ)
    cwd = os.getcwd()
    monkeypatch.setattr(bridge_bootstrap, 'main', lambda args: seen.append(args) or 3)
    assert launcher().main(['--run']) == 3
    assert seen[0][-5:] == ['--run', '--state-dir', '/home/kali/agent-dialog-beta/state', '--port', '8790']
    assert sys.path == previous_path and sys.argv == previous_argv
    assert dict(os.environ) == previous_env and os.getcwd() == cwd


@pytest.mark.parametrize('args', [['--state-dir', 'other'], ['--port', '8789'], ['--source', 'other']])
def test_no_message_selected_overrides(monkeypatch, args):
    monkeypatch.setattr(bridge_bootstrap, 'main', lambda args: pytest.fail('bootstrap'))
    with pytest.raises(SystemExit): launcher().main(args)


def test_path_restored_on_bootstrap_error(monkeypatch):
    previous = list(sys.path)
    def fail(args): raise RuntimeError('fixture')
    monkeypatch.setattr(bridge_bootstrap, 'main', fail)
    with pytest.raises(RuntimeError): launcher().main([])
    assert sys.path == previous


def test_dropin_changes_only_start_and_restart_policy():
    lines = [line for line in (ROOT / 'deploy/90-agent-dialog-beta.conf').read_text().splitlines()
             if line and not line.startswith('#')]
    assert lines == ['[Service]', 'ExecStart=',
        'ExecStart=/usr/bin/env -u APPDIR -u APPIMAGE /home/kali/.venvs/bridge-clean/bin/python '
        '/home/kali/agent-dialog-beta/code/deploy/hermes-beta-launcher.py --run', 'Restart=no']


def test_explicit_state_validation_is_not_run(monkeypatch):
    seen = []
    monkeypatch.setattr(bridge_bootstrap, 'main', lambda args: seen.append(args) or 0)
    assert launcher().main(['--validate-state']) == 0
    assert seen[0][-5:] == ['--validate-state', '--state-dir',
                            '/home/kali/agent-dialog-beta/state', '--port', '8790']
    assert '--run' not in seen[0]


def test_run_and_validation_are_mutually_exclusive(monkeypatch):
    monkeypatch.setattr(bridge_bootstrap, 'main', lambda args: pytest.fail('bootstrap'))
    with pytest.raises(SystemExit): launcher().main(['--run', '--validate-state'])
