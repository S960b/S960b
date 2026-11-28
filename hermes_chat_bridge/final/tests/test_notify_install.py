import hashlib
import json
import os

import pytest

from git_dialog import notify_install as install


@pytest.fixture
def layout(tmp_path, monkeypatch):
    directory = tmp_path / 'source'
    base = tmp_path / 'beta'
    state = base / 'state'
    for path in (directory, base, state):
        path.mkdir(mode=0o700)
    for name, path in [('DIRECTORY', directory), ('BASE', base), ('STATE', state),
                       ('MARKER', state / 'bridge-wakeup.enabled')]:
        monkeypatch.setattr(install, name, path)
    for name, parent in [('SOURCES', directory), ('BACKUPS', base), ('ARCHIVES', base),
                         ('STAGES', directory), ('RESTORES', directory)]:
        monkeypatch.setattr(install, name, {kind: parent / (kind + '-' + name) for kind in install.KINDS})
    before = {kind: ('# retained dirty ' + kind + '\nraise AssertionError("never execute")\n').encode()
              for kind in install.KINDS}
    after = {kind: raw + b'# reviewed notify hook\n' for kind, raw in before.items()}
    candidates = {}
    for kind in install.KINDS:
        install.SOURCES[kind].write_bytes(before[kind])
        install.SOURCES[kind].chmod(0o644)
        candidates[kind] = tmp_path / (kind + '-candidate')
        candidates[kind].write_bytes(after[kind])
        candidates[kind].chmod(0o600)
    install.MARKER.write_bytes(b'')
    install.MARKER.chmod(0o600)
    for name, data in [('BEFORE', before), ('AFTER', after)]:
        monkeypatch.setattr(install, name, {kind: hashlib.sha256(raw).hexdigest() for kind, raw in data.items()})
    return candidates, before, after


def invoke(action, candidates):
    return install.run(action, candidates['bridge'], candidates['cli'])


def test_check_reads_only_and_never_executes(layout):
    candidates, before, _ = layout
    marker = install.MARKER.stat()
    result = invoke('check', candidates)
    assert result == {'status': 'notify_install_checked', 'live_sources_modified': False,
                      'activation_modified': False, 'service_restarted': False}
    assert install.MARKER.stat() == marker
    assert all(install.SOURCES[kind].read_bytes() == before[kind] for kind in install.KINDS)
    assert not any(path.exists() for path in install.BACKUPS.values())


@pytest.mark.parametrize('mask', [0o002, 0o077, 0o777])
def test_two_source_install_byte_exact_rollback_no_marker_change(layout, mask):
    candidates, before, after = layout
    marker = install.MARKER.stat()
    previous = os.umask(mask)
    try:
        result = invoke('install', candidates)
        assert result['status'] == 'notify_pair_installed'
        assert result['completed_replacements'] == ['bridge', 'cli']
        assert result['service_restarted'] is False
        for kind in install.KINDS:
            assert install.SOURCES[kind].read_bytes() == after[kind]
            assert install.SOURCES[kind].stat().st_mode & 0o777 == 0o644
            assert install.BACKUPS[kind].read_bytes() == before[kind]
            assert install.BACKUPS[kind].stat().st_mode & 0o777 == 0o600
        result = install.run('rollback')
    finally:
        os.umask(previous)
    assert result['status'] == 'notify_pair_restored'
    assert result['completed_replacements'] == ['cli', 'bridge']
    assert install.MARKER.stat() == marker
    for kind in install.KINDS:
        assert install.SOURCES[kind].read_bytes() == before[kind]
        assert install.SOURCES[kind].stat().st_mode & 0o777 == 0o644
        assert install.BACKUPS[kind].read_bytes() == before[kind]
        assert install.ARCHIVES[kind].read_bytes() == after[kind]
        assert install.ARCHIVES[kind].stat().st_mode & 0o777 == 0o600
        assert candidates[kind].read_bytes() == after[kind]
    assert invoke('install', candidates)['phase'] == 'guards'  # Never reuse artifacts.


@pytest.mark.parametrize('group', ['BACKUPS', 'ARCHIVES', 'STAGES', 'RESTORES'])
@pytest.mark.parametrize('kind', install.KINDS)
def test_any_occupied_artifact_even_dangling_link_stops_before_writes(layout, group, kind):
    candidates, before, _ = layout
    path = getattr(install, group)[kind]
    path.symlink_to(path.parent / 'missing')
    assert invoke('install', candidates)['phase'] == 'guards'
    assert path.is_symlink()
    assert all(install.SOURCES[k].read_bytes() == before[k] for k in install.KINDS)


@pytest.mark.parametrize('kind', install.KINDS)
@pytest.mark.parametrize('bad', ['hash', 'mode', 'hardlink', 'symlink', 'oversize', 'owner'])
def test_source_guards_before_backup(layout, monkeypatch, kind, bad):
    candidates, _, _ = layout
    source = install.SOURCES[kind]
    if bad == 'hash': source.write_bytes(b'changed')
    if bad == 'mode': source.chmod(0o664)
    if bad == 'hardlink': (source.parent / 'extra').hardlink_to(source)
    if bad == 'symlink':
        source.unlink()
        source.symlink_to(candidates[kind])
    if bad == 'oversize': source.write_bytes(b'x' * (512 * 1024 + 1))
    if bad == 'owner':
        uid = os.getuid()
        monkeypatch.setattr(os, 'getuid', lambda: uid + 1)
    assert invoke('install', candidates)['phase'] == 'guards'
    assert not any(p.exists() for p in install.BACKUPS.values())


@pytest.mark.parametrize('kind', install.KINDS)
@pytest.mark.parametrize('bad', ['hash', 'mode', 'hardlink', 'symlink'])
def test_both_candidates_checked_before_any_writes(layout, kind, bad):
    candidates, before, _ = layout
    path = candidates[kind]
    if bad == 'hash': path.write_bytes(b'changed')
    if bad == 'mode': path.chmod(0o644)
    if bad == 'hardlink': (path.parent / 'extra').hardlink_to(path)
    if bad == 'symlink':
        path.unlink()
        path.symlink_to(install.SOURCES[kind])
    assert invoke('install', candidates)['phase'] == 'guards'
    assert not any(p.exists() for p in install.BACKUPS.values())
    assert all(install.SOURCES[k].read_bytes() == before[k] for k in install.KINDS)


@pytest.mark.parametrize('bad', ['missing', 'mode', 'content', 'hardlink', 'symlink'])
def test_existing_activation_required_never_changed(layout, bad):
    candidates, _, _ = layout
    marker = install.MARKER
    if bad == 'missing': marker.unlink()
    if bad == 'mode': marker.chmod(0o644)
    if bad == 'content': marker.write_bytes(b'not empty')
    if bad == 'hardlink': (marker.parent / 'linked').hardlink_to(marker)
    if bad == 'symlink':
        marker.unlink()
        marker.symlink_to(marker.parent / 'missing')
    assert invoke('install', candidates)['phase'] == 'guards'
    assert not any(p.exists() for p in install.BACKUPS.values())


@pytest.mark.parametrize('which', ['source', 'marker'])
def test_concurrent_edit_after_staging_stops_preserves_artifacts(layout, monkeypatch, which):
    candidates, before, after = layout
    original = install._new
    def change(path, raw, mode):
        original(path, raw, mode)
        if path == install.STAGES['cli']:
            if which == 'source': install.SOURCES['cli'].write_bytes(b'unrelated edit')
            else:
                install.MARKER.unlink()
                install.MARKER.write_bytes(b'')
                install.MARKER.chmod(0o600)
    monkeypatch.setattr(install, '_new', change)
    result = invoke('install', candidates)
    assert result['phase'] == 'bridge_replace'
    assert result['completed_replacements'] == []
    assert install.SOURCES['bridge'].read_bytes() == before['bridge']
    assert all(install.BACKUPS[k].read_bytes() == before[k] for k in install.KINDS)
    assert all(install.STAGES[k].read_bytes() == after[k] for k in install.KINDS)


@pytest.mark.parametrize('fail_at', [1, 2])
def test_replace_failure_reports_partial_state_never_retries_or_auto_rolls_back(layout, monkeypatch, fail_at):
    candidates, before, after = layout
    replace = os.replace
    calls = []
    def fail(source, target):
        calls.append(target)
        if len(calls) == fail_at: raise OSError('secret/config text')
        replace(source, target)
    monkeypatch.setattr(os, 'replace', fail)
    result = invoke('install', candidates)
    assert result == {'error': 'notify_install_failed', 'action': 'install',
                      'phase': ('bridge' if fail_at == 1 else 'cli') + '_replace',
                      'completed_replacements': [] if fail_at == 1 else ['bridge']}
    assert len(calls) == fail_at
    assert install.SOURCES['cli'].read_bytes() == before['cli']
    assert install.SOURCES['bridge'].read_bytes() == (before if fail_at == 1 else after)['bridge']
    assert install.run('rollback')['phase'] == 'guards'  # Mixed state is not guessed/repaired.


def test_post_rename_sync_failure_records_completed_rename(layout, monkeypatch):
    candidates, _, after = layout
    def fail(_): raise OSError('not forwarded')
    monkeypatch.setattr(install, '_sync', fail)
    result = invoke('install', candidates)
    assert result['completed_replacements'] == ['bridge']
    assert result['phase'] == 'bridge_replace'
    assert install.SOURCES['bridge'].read_bytes() == after['bridge']


def test_rollback_corrupt_backup_stops_before_archives(layout):
    candidates, _, after = layout
    assert invoke('install', candidates)['status'] == 'notify_pair_installed'
    install.BACKUPS['cli'].write_bytes(b'corrupt')
    assert install.run('rollback')['phase'] == 'guards'
    assert not any(p.exists() for p in install.ARCHIVES.values())
    assert all(install.SOURCES[k].read_bytes() == after[k] for k in install.KINDS)


@pytest.mark.parametrize('action,args', [('invalid', ()), ('check', ()), ('install', ('one',)),
                                         ('rollback', ('unwanted', 'args'))])
def test_options_fail_before_io(action, args):
    assert install.run(action, *args)['phase'] == 'options'


def test_cli_only_safe_json(layout, capsys):
    candidates, _, _ = layout
    assert install.main(['check', '--bridge-candidate', str(candidates['bridge']),
                         '--cli-candidate', str(candidates['cli'])]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'notify_install_checked'
    assert install.main(['install']) == 1
    assert json.loads(capsys.readouterr().out) == {
        'error': 'notify_install_failed', 'action': 'install', 'phase': 'options',
        'completed_replacements': []}
