import hashlib

import pytest

from git_dialog import DialogError
from git_dialog import gateway_runtime_patch as patch

SOURCE = '''def build_app(base_url):
    from git_dialog.native_gateway import register_mcp
    bridge = object()
    mcp = make_mcp_server(base_url, bridge)
    register_mcp(mcp, bridge)
    app = mcp.streamable_http_app()
    original_lifespan = app.router.lifespan_context
    async def lifespan(app):
        async with original_lifespan(app):
            yield
    app.router.lifespan_context = lifespan
    return app
'''


@pytest.fixture
def paths(tmp_path, monkeypatch):
    base, directory = tmp_path/'base', tmp_path/'source'
    base.mkdir(mode=0o700); directory.mkdir(mode=0o700)
    source = directory/'bridge.py'; source.write_text(SOURCE); source.chmod(0o644)
    for key,value in {'BASE':base,'SOURCE':source,'BACKUP':base/'backup.py','STAGE':directory/'.stage',
        'ARCHIVE':base/'archive.py','RESTORE':directory/'.restore',
        'BEFORE':hashlib.sha256(SOURCE.encode()).hexdigest()}.items(): monkeypatch.setattr(patch,key,value)
    return source


def test_exact_lifespan_insertion_after_original_binding_only():
    out = patch.rewrite(SOURCE)
    added = out[out.index('    from git_dialog.gateway_lifespan'):out.index('    return app')]
    assert out.replace(added,'') == SOURCE
    assert 'dialog_factory=dialog_factory_from_systemd' in added
    assert 'os.environ' not in added and 'LoadCredential' not in added


@pytest.mark.parametrize('value', [SOURCE.replace('app.router.lifespan_context = lifespan','x = lifespan'),
    SOURCE.replace('    return app','    app.router.lifespan_context = lifespan\n    return app'),
    SOURCE.replace('git_dialog.native_gateway','other'), SOURCE.replace('\n','\r\n')])
def test_changed_native_shape_stops_without_guess(value):
    with pytest.raises(DialogError): patch.rewrite(value)


def test_new_source_backup_then_rollback_to_registration_bridge(paths):
    before = paths.read_bytes()
    assert patch.run('check')['status'] == 'gateway_runtime_checked'
    assert not patch.BACKUP.exists() and paths.read_bytes() == before
    out = patch.run('install')
    assert out['status'] == 'gateway_runtime_installed' and out['completed_replacements'] == ['bridge']
    assert patch.BACKUP.read_bytes() == before and patch.BACKUP.stat().st_mode & 0o777 == 0o600
    after = paths.read_bytes()
    assert patch.run('rollback')['status'] == 'gateway_runtime_restored'
    assert paths.read_bytes() == before and patch.ARCHIVE.read_bytes() == after


@pytest.mark.parametrize('name',['BACKUP','STAGE','ARCHIVE','RESTORE'])
def test_existing_artifact_prevents_install(paths, name):
    target = getattr(patch,name); target.write_text('existing')
    assert patch.run('install')['completed_replacements'] == []
    assert paths.read_text() == SOURCE and target.read_text() == 'existing'


def test_postrename_sync_failure_is_not_hidden(paths, monkeypatch):
    sync = patch._sync
    def fail(path):
        if path == paths.parent and not patch.STAGE.exists() and 'gateway_lifespan' in paths.read_text():
            raise OSError('private')
        sync(path)
    monkeypatch.setattr(patch,'_sync',fail)
    result = patch.run('install')
    assert result['phase'] == 'directory_sync' and result['completed_replacements'] == ['bridge']
    assert patch.BACKUP.exists()
