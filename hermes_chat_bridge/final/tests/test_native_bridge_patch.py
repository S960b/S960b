import ast
import hashlib
import sys
from types import SimpleNamespace

import pytest

from git_dialog import DialogError, bridge_patch as patch, wakeup_forward, wakeup_key
from test_bridge_patch import SOURCE


NATIVE = SOURCE.replace('def build_app(base):\n',
    'OWNER_PASS = "fixture-owner-value"\ndef build_app(base):\n'
    '    if not OWNER_PASS:\n        raise RuntimeError("BRIDGE_PASS is required")\n').replace(
    '    bridge = app.state.bridge\n', '    bridge = app.state.bridge\n    app.state.bridge = bridge\n')
NATIVE += '\ndef main():\n    pass\n\nif __name__ == "__main__":\n    main()\n'


def transformed(tmp_path, monkeypatch):
    state = tmp_path / 'state'
    state.mkdir(mode=0o700)
    monkeypatch.setattr(patch, 'NATIVE_STATE', str(state))
    raw = patch._rewrite(NATIVE, native=True)
    namespace = {'__name__': 'trusted_native_fixture'}
    exec(compile(raw, '<trusted-native-fixture>', 'exec'), namespace)
    return state, raw, namespace


def test_native_entry_auth_guard_and_dirty_lines_are_preserved(tmp_path, monkeypatch):
    state, raw, module = transformed(tmp_path, monkeypatch)
    ast.parse(raw)
    assert 'PRESERVE_EXISTING_CHANGE' in raw
    assert 'if not OWNER_PASS:\n        raise RuntimeError("BRIDGE_PASS is required")' in raw
    assert raw.endswith('if __name__ == "__main__":\n    main()\n')
    assert raw.index('bridge.configure_beta_wakeup()') > raw.index('app.state.bridge = bridge')
    assert raw.index('bridge.configure_beta_wakeup()') < raw.index('async def lifespan')
    module['OWNER_PASS'] = ''
    with pytest.raises(RuntimeError, match='BRIDGE_PASS is required'):
        module['build_app']('fixture')


def test_default_off_does_not_load_key_import_package_or_change_path(tmp_path, monkeypatch):
    state, raw, module = transformed(tmp_path, monkeypatch)
    bridge = module['BridgeApp']('fixture')
    monkeypatch.setattr(wakeup_key, 'load', lambda *a: pytest.fail('key read'))
    before = list(sys.path)
    bridge.configure_beta_wakeup()
    assert bridge._beta_forwarder is None and sys.path == before


@pytest.mark.parametrize('bad', ['content', 'mode', 'symlink', 'directory', 'hardlink', 'owner'])
def test_activation_marker_fail_closed(tmp_path, monkeypatch, bad):
    state, raw, module = transformed(tmp_path, monkeypatch)
    marker = state / 'bridge-wakeup.enabled'
    if bad == 'directory': marker.mkdir(mode=0o700)
    elif bad == 'symlink': marker.symlink_to(state / 'missing')
    else:
        marker.touch(mode=0o600)
        if bad == 'content': marker.write_bytes(b'not-configuration')
        if bad == 'mode': marker.chmod(0o644)
        if bad == 'hardlink': (state / 'link').hardlink_to(marker)
        if bad == 'owner':
            import os
            real = os.getuid()
            monkeypatch.setattr(os, 'getuid', lambda: real + 1)
    bridge = module['BridgeApp']('fixture')
    monkeypatch.setattr(wakeup_key, 'load', lambda *a: pytest.fail('key read'))
    with pytest.raises((RuntimeError, OSError)): bridge.configure_beta_wakeup()
    assert bridge._beta_forwarder is None


def test_explicit_marker_configures_but_does_not_start_forwarder(tmp_path, monkeypatch):
    state, raw, module = transformed(tmp_path, monkeypatch)
    (state / 'bridge-wakeup.enabled').touch(mode=0o600)
    seen = []
    key = b'x' * 32
    monkeypatch.setattr(wakeup_key, 'load', lambda path: seen.append(path) or key)
    monkeypatch.setattr(wakeup_forward, 'WakeupForwarder',
                        lambda actual, **kw: seen.append((actual, kw)) or 'configured-not-started')
    bridge = module['BridgeApp']('fixture')
    before = list(sys.path)
    bridge.configure_beta_wakeup()
    assert bridge._beta_forwarder == 'configured-not-started'
    assert seen == [str(state), (key, {'port': 8790})]
    assert sys.path == before


def test_native_build_activation_failure_releases_original_lock(tmp_path, monkeypatch):
    state, raw, module = transformed(tmp_path, monkeypatch)
    closed = []
    def fail(): raise RuntimeError('fixture-activation')
    bridge = SimpleNamespace(configure_beta_wakeup=fail)
    app = SimpleNamespace(state=SimpleNamespace(bridge=bridge,
        pilot_lock=SimpleNamespace(close=lambda: closed.append(True))),
        router=SimpleNamespace(lifespan_context=None))
    module['existing_build'] = lambda base: app
    with pytest.raises(RuntimeError, match='fixture-activation'): module['build_app']('fixture')
    assert closed == [True]


def test_native_import_failure_restores_path_without_reading_other_credentials(tmp_path, monkeypatch):
    state, raw, module = transformed(tmp_path, monkeypatch)
    bridge = module['BridgeApp']('fixture')
    def fail(*a): raise RuntimeError('fixture-key-validation')
    monkeypatch.setattr(wakeup_key, 'load', fail)
    before = list(sys.path)
    with pytest.raises(RuntimeError): bridge.enable_beta_wakeup(str(state))
    assert sys.path == before and bridge._beta_forwarder is None


def test_native_requires_unique_known_app_assignment():
    with pytest.raises(DialogError): patch._rewrite(SOURCE, native=True)
    changed = NATIVE.replace('    app.state.bridge = bridge\n',
                            '    app.state.bridge = bridge\n    app.state.bridge = bridge\n')
    with pytest.raises(DialogError): patch._rewrite(changed, native=True)


def test_native_candidate_hash_guard_and_no_reapply():
    with pytest.raises(DialogError, match='source_changed'): patch.candidate(NATIVE.encode(), native=True)
    with pytest.raises(DialogError): patch._rewrite(patch._rewrite(NATIVE, native=True), native=True)


@pytest.mark.parametrize('creation_umask', [0o002, 0o022, 0o077])
def test_native_cli_creates_only_new_candidate_no_marker_or_live_write(tmp_path, monkeypatch, capsys, creation_umask):
    import json
    import os
    source = tmp_path / 'bridge_server.py'
    previous_umask = os.umask(creation_umask)
    try:
        source.write_text(NATIVE)
    finally:
        os.umask(previous_umask)
    # The success fixture must satisfy the production no-group/other-write guard,
    # independently of the test runner's inherited umask. Never repair live files.
    source.chmod(0o600)
    output = tmp_path / 'native-candidate.py'
    monkeypatch.setattr(patch, 'EXPECTED', hashlib.sha256(source.read_bytes()).hexdigest())
    assert patch.main(['--source', str(source), '--output', str(output), '--native-entry']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['native_entry_preserved'] and not result['wakeup_default_enabled']
    assert not result['live_source_modified'] and source.read_text() == NATIVE
    assert not (tmp_path / 'bridge-wakeup.enabled').exists()
    before = output.read_bytes()
    assert patch.main(['--source', str(source), '--output', str(output), '--native-entry']) == 1
    assert output.read_bytes() == before


@pytest.mark.parametrize('bad', ['writable', 'hardlink', 'owner'])
def test_native_cli_source_metadata_guard(tmp_path, monkeypatch, bad):
    import os
    source = tmp_path / 'bridge_server.py'; source.write_text(NATIVE)
    source.chmod(0o600)
    if bad == 'writable': source.chmod(0o664)
    if bad == 'hardlink': (tmp_path / 'link').hardlink_to(source)
    if bad == 'owner':
        real = os.getuid()
        monkeypatch.setattr(os, 'getuid', lambda: real + 1)
    monkeypatch.setattr(patch, 'EXPECTED', hashlib.sha256(source.read_bytes()).hexdigest())
    output = tmp_path / 'candidate.py'
    assert patch.main(['--source', str(source), '--output', str(output), '--native-entry']) == 1
    assert not output.exists()


def test_native_restart_policy_does_not_replace_startup_or_credentials():
    from pathlib import Path
    root = Path(__file__).absolute().parent.parent
    lines = [line for line in (root / 'deploy/91-agent-dialog-beta-native.conf').read_text().splitlines()
             if line and not line.startswith('#')]
    assert lines == ['[Service]', 'Restart=no']
