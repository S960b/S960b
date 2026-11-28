import hashlib
from pathlib import Path

import pytest

from git_dialog import DialogError
from git_dialog import gateway_patch as patch

SOURCE = '''def make_mcp_server(base_url, bridge):
    return object()

def build_app(base_url):
    bridge = object()
    mcp = make_mcp_server(base_url, bridge)
    app = mcp.streamable_http_app()
    return app
'''


@pytest.fixture
def paths(tmp_path, monkeypatch):
    directory = tmp_path / 'source'; directory.mkdir(mode=0o700)
    base = tmp_path / 'base'; base.mkdir(mode=0o700)
    source = directory / 'bridge_server.py'; source.write_text(SOURCE); source.chmod(0o644)
    for name, value in {'SOURCE': source, 'BASE': base, 'BACKUP': base/'backup.py',
        'STAGE': directory/'.stage', 'ARCHIVE': base/'archive.py', 'RESTORE': directory/'.restore',
        'BEFORE': hashlib.sha256(SOURCE.encode()).hexdigest()}.items():
        monkeypatch.setattr(patch, name, value)
    return source


def test_insertion_only_original_bytes_preserved_and_no_auth_or_service_edits():
    result = patch.rewrite(SOURCE)
    marker = result[result.index('    import sys\n'):result.index('    app =')]
    assert result.replace(marker, '') == SOURCE
    assert 'register_mcp(mcp, bridge)' in marker and 'finally:' in marker
    assert 'sys.path[:] = gateway_previous_path' in marker


@pytest.mark.parametrize('source', [SOURCE.replace('base_url, bridge)', 'bridge, base_url)'),
    SOURCE.replace('    mcp = make_mcp_server(base_url, bridge)\n', ''),
    SOURCE.replace('    app =', '    mcp = make_mcp_server(base_url, bridge)\n    app ='),
    SOURCE.replace('\n', '\r\n'), SOURCE.rstrip('\n')])
def test_changed_shape_does_not_guess(source):
    with pytest.raises(DialogError): patch.rewrite(source)


def test_check_read_only_install_new_backup_then_strict_rollback(paths):
    source = paths
    assert patch.run('check')['status'] == 'gateway_install_checked'
    assert source.read_text() == SOURCE and not patch.BACKUP.exists()
    installed = patch.run('install')
    assert installed['status'] == 'gateway_bridge_installed'
    assert installed['completed_replacements'] == ['bridge']
    assert patch.BACKUP.read_text() == SOURCE and patch.BACKUP.stat().st_mode & 0o777 == 0o600
    assert source.read_text() == patch.rewrite(SOURCE) and source.stat().st_mode & 0o777 == 0o644
    assert patch.run('install')['error'] == 'gateway_patch_failed'  # No overwrite/retry.
    assert patch.run('rollback')['status'] == 'gateway_bridge_restored'
    assert source.read_text() == SOURCE and patch.ARCHIVE.read_text() == patch.rewrite(SOURCE)


def test_rename_reported_even_if_directory_fsync_fails(paths, monkeypatch):
    original = patch._sync
    def failed(path):
        if path == paths.parent and not patch.STAGE.exists() and 'native_gateway' in paths.read_text():
            raise OSError('PRIVATE_EXCEPTION')
        return original(path)
    monkeypatch.setattr(patch, '_sync', failed)
    result = patch.run('install')
    assert result == {'error':'gateway_patch_failed', 'action':'install',
                      'phase':'directory_sync', 'completed_replacements':['bridge']}
    assert patch.BACKUP.exists() and 'native_gateway' in paths.read_text()


@pytest.mark.parametrize('target', ['BACKUP', 'STAGE', 'ARCHIVE', 'RESTORE'])
def test_existing_artifacts_never_reused(paths, target):
    path = getattr(patch, target); path.write_text('existing')
    assert patch.run('install')['error'] == 'gateway_patch_failed'
    assert path.read_text() == 'existing' and paths.read_text() == SOURCE


def test_changed_source_rejects_before_backup(paths):
    paths.write_text(SOURCE+'#changed\n')
    assert patch.run('install')['completed_replacements'] == []
    assert not patch.BACKUP.exists()
