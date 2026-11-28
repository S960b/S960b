import hashlib
import json
import os

import pytest

from git_dialog import native_install as install


@pytest.fixture
def layout(tmp_path, monkeypatch):
    source_dir = tmp_path / 'native'
    source_dir.mkdir(mode=0o700)
    base = tmp_path / 'beta'
    base.mkdir(mode=0o700)
    state = base / 'state'
    state.mkdir(mode=0o700)
    paths = {'SOURCE': source_dir / 'bridge_server.py', 'BASE': base, 'STATE': state,
             'BACKUP': base / 'original.py', 'ARCHIVE': base / 'native-rollback.py',
             'STAGE': source_dir / '.stage', 'RESTORE': source_dir / '.restore',
             'MARKER': state / 'bridge-wakeup.enabled', 'DISABLED': state / 'disabled'}
    for name, path in paths.items():
        monkeypatch.setattr(install, name, path)
    original = b'# retained dirty changes\nraise AssertionError("never execute")\n'
    native = original + b'# reviewed hooks\n'
    paths['SOURCE'].write_bytes(original)
    paths['SOURCE'].chmod(0o644)
    candidate = tmp_path / 'candidate.py'
    candidate.write_bytes(native)
    candidate.chmod(0o600)
    monkeypatch.setattr(install, 'ORIGINAL', hashlib.sha256(original).hexdigest())
    monkeypatch.setattr(install, 'NATIVE', hashlib.sha256(native).hexdigest())
    monkeypatch.setattr(install, '_prerequisites', lambda: pytest.fail('unexpected credential read'))
    return paths, candidate, original, native


def test_check_is_read_only_no_key_or_service(layout):
    paths, candidate, original, native = layout
    before = set(paths['BASE'].rglob('*'))
    assert install.run('check', candidate)['status'] == 'native_install_checked'
    assert set(paths['BASE'].rglob('*')) == before
    assert paths['SOURCE'].read_bytes() == original
    assert not paths['STAGE'].exists()


@pytest.mark.parametrize('mask', [0o002, 0o077, 0o777])
def test_install_and_byte_exact_rollback_without_restart(layout, mask):
    paths, candidate, original, native = layout
    previous = os.umask(mask)
    try:
        result = install.run('install', candidate)
    finally:
        os.umask(previous)
    assert result == {'status': 'native_installed', 'backup_verified': True,
                      'wakeup_enabled': False, 'service_restarted': False}
    assert paths['SOURCE'].read_bytes() == native
    assert paths['SOURCE'].stat().st_mode & 0o777 == 0o644
    assert paths['BACKUP'].read_bytes() == original
    assert paths['BACKUP'].stat().st_mode & 0o777 == 0o600
    assert not paths['MARKER'].exists()
    assert install.run('rollback')['status'] == 'native_original_restored'
    assert paths['SOURCE'].read_bytes() == original
    assert paths['SOURCE'].stat().st_mode & 0o777 == 0o644
    assert paths['ARCHIVE'].read_bytes() == native
    assert paths['BACKUP'].read_bytes() == original
    assert 'error' in install.run('install', candidate)  # Never overwrite retained artifacts.


@pytest.mark.parametrize('name', ['BACKUP', 'ARCHIVE', 'STAGE', 'RESTORE', 'MARKER', 'DISABLED'])
def test_occupied_destinations_even_dangling_symlink_block_before_writes(layout, name):
    paths, candidate, original, native = layout
    paths[name].symlink_to(paths['BASE'] / 'missing')
    assert install.run('install', candidate)['phase'] == 'guards'
    assert paths['SOURCE'].read_bytes() == original
    assert paths[name].is_symlink()


@pytest.mark.parametrize('bad', ['hash', 'mode', 'hardlink', 'symlink', 'owner', 'oversize'])
def test_source_guard_no_backup_on_mismatch(layout, monkeypatch, bad):
    paths, candidate, original, native = layout
    source = paths['SOURCE']
    if bad == 'hash': source.write_bytes(b'changed')
    if bad == 'mode': source.chmod(0o664)
    if bad == 'hardlink': (source.parent / 'linked').hardlink_to(source)
    if bad == 'symlink':
        source.unlink()
        source.symlink_to(candidate)
    if bad == 'owner':
        uid = os.getuid()
        monkeypatch.setattr(os, 'getuid', lambda: uid + 1)
    if bad == 'oversize': source.write_bytes(b'x' * (install.LIMIT + 1))
    assert install.run('install', candidate)['phase'] == 'guards'
    assert not paths['BACKUP'].exists()


def test_candidate_mismatch_no_live_change(layout):
    paths, candidate, original, native = layout
    candidate.write_bytes(b'unreviewed')
    assert 'error' in install.run('install', candidate)
    assert paths['SOURCE'].read_bytes() == original
    assert not paths['BACKUP'].exists()


def test_changed_source_between_backup_and_replace_stops_preserves_all(layout, monkeypatch):
    paths, candidate, original, native = layout
    real_new = install._new
    def changed(path, raw, mode):
        real_new(path, raw, mode)
        if path == paths['STAGE']:
            paths['SOURCE'].write_bytes(b'unrelated concurrent edit')
    monkeypatch.setattr(install, '_new', changed)
    assert install.run('install', candidate)['phase'] == 'replace'
    assert paths['SOURCE'].read_bytes() == b'unrelated concurrent edit'
    assert paths['BACKUP'].read_bytes() == original
    assert paths['STAGE'].read_bytes() == native


def test_replace_failure_no_auto_repair_or_retry(layout, monkeypatch):
    paths, candidate, original, native = layout
    calls = []
    def fail(*args):
        calls.append(args)
        raise OSError('secret/config/credential text must never escape')
    monkeypatch.setattr(os, 'replace', fail)
    assert install.run('install', candidate) == {'error': 'native_install_failed',
                                                'phase': 'replace', 'action': 'install'}
    assert len(calls) == 1
    assert paths['SOURCE'].read_bytes() == original
    assert paths['BACKUP'].read_bytes() == original
    assert paths['STAGE'].read_bytes() == native


def test_explicit_activation_validates_then_retains_disabled_marker(layout, monkeypatch):
    paths, candidate, original, native = layout
    assert install.run('install', candidate)['status'] == 'native_installed'
    called = []
    monkeypatch.setattr(install, '_prerequisites', lambda: called.append(True))
    assert install.run('activate')['status'] == 'native_activation_created'
    assert called == [True]
    assert paths['MARKER'].read_bytes() == b''
    assert paths['MARKER'].stat().st_mode & 0o777 == 0o600
    assert install.run('rollback')['phase'] == 'guards'  # Must explicitly deactivate first.
    assert install.run('activate')['phase'] == 'guards'
    assert called == [True]
    assert install.run('deactivate')['status'] == 'native_activation_retained_disabled'
    assert not paths['MARKER'].exists()
    assert paths['DISABLED'].read_bytes() == b''
    assert paths['DISABLED'].stat().st_nlink == 1
    assert install.run('rollback')['status'] == 'native_original_restored'
    assert paths['SOURCE'].read_bytes() == original


def test_failed_prerequisites_create_no_activation(layout, monkeypatch):
    paths, candidate, original, native = layout
    install.run('install', candidate)
    def fail(): raise RuntimeError('sensitive text')
    monkeypatch.setattr(install, '_prerequisites', fail)
    assert install.run('activate')['phase'] == 'prerequisites'
    assert not paths['MARKER'].exists()
    assert paths['SOURCE'].read_bytes() == native


def test_bad_marker_never_moved_or_rollbacked(layout):
    paths, candidate, original, native = layout
    install.run('install', candidate)
    paths['MARKER'].write_bytes(b'not our marker')
    paths['MARKER'].chmod(0o600)
    assert install.run('deactivate')['phase'] == 'guards'
    assert paths['MARKER'].read_bytes() == b'not our marker'
    assert not paths['DISABLED'].exists()


def test_corrupt_original_backup_cannot_rollback(layout):
    paths, candidate, original, native = layout
    install.run('install', candidate)
    paths['BACKUP'].write_bytes(b'corrupt')
    assert install.run('rollback')['phase'] == 'guards'
    assert paths['SOURCE'].read_bytes() == native
    assert not paths['ARCHIVE'].exists()


def test_directory_symlink_rejected(layout, monkeypatch):
    paths, candidate, original, native = layout
    link = paths['BASE'].parent / 'linked-beta'
    link.symlink_to(paths['BASE'], target_is_directory=True)
    monkeypatch.setattr(install, 'BASE', link)
    assert install.run('check', candidate)['phase'] == 'guards'


def test_cli_safe_json_and_option_rejection(layout, capsys):
    paths, candidate, original, native = layout
    assert install.main(['check', '--candidate', str(candidate)]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'native_install_checked'
    assert install.main(['activate', '--candidate', str(candidate)]) == 1
    assert json.loads(capsys.readouterr().out) == {'error': 'native_install_options_rejected'}
    assert install.main(['install']) == 1
    assert json.loads(capsys.readouterr().out) == {'error': 'native_install_options_rejected'}
