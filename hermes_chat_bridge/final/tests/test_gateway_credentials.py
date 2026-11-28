import json
import os
from pathlib import Path

import pytest

from git_dialog import DialogError
from git_dialog import gateway_credentials as module


@pytest.fixture
def paths(tmp_path, monkeypatch):
    base, dropins = tmp_path/'base', tmp_path/'dropins'
    base.mkdir(mode=0o700); dropins.mkdir(mode=0o700)
    for key, value in {'BASE':base,'DROPINS':dropins,'DROPIN':dropins/'92-test.conf',
            'STAGE':dropins/'.stage','ARCHIVE':base/'92-archive.conf'}.items():
        monkeypatch.setattr(module, key, value)
    source = tmp_path/'configured'; source.write_text('test-only-noncredential\n'); source.chmod(0o600)
    monkeypatch.setattr('git_dialog.service._interactive_secret', lambda prompt: str(source))
    return source


def test_new_private_dropin_and_strict_archival_rollback(paths):
    before = paths.read_bytes()
    assert module.run('check')['status'] == 'gateway_credential_checked'
    out = module.run('install')
    assert out['status'] == 'gateway_credential_installed' and out['completed_dropins'] == ['credential']
    raw = module.DROPIN.read_bytes()
    assert module._unit(raw) == raw and module.DROPIN.stat().st_mode & 0o777 == 0o600
    assert b'LoadCredential=authority-git-pat:' in raw
    assert b'ExecStart' not in raw and b'Environment=' not in raw and b'Restart=' not in raw
    assert paths.read_bytes() == before and not module.STAGE.exists()
    assert module.run('install')['completed_dropins'] == []
    assert module.run('rollback')['completed_dropins'] == ['credential_removed']
    assert not module.DROPIN.exists() and module.ARCHIVE.read_bytes() == raw
    assert paths.read_bytes() == before


@pytest.mark.parametrize('value', ['/tmp/a b','/tmp/x%h','/tmp/a\nRestart=always','/tmp/../x','relative', '/tmp/x"'])
def test_source_path_never_interpreted_or_escaped(value):
    with pytest.raises(DialogError): module._source_path(value)


def test_install_source_mode_mismatch_does_not_repair_or_write(paths):
    paths.chmod(0o644)
    assert module.run('install')['phase'] == 'source_validation'
    assert not module.STAGE.exists() and not module.DROPIN.exists()
    assert paths.stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize('name', ['DROPIN','STAGE','ARCHIVE'])
def test_existing_artifact_never_overwritten(paths, name):
    path = getattr(module, name); path.write_text('existing')
    assert module.run('install')['error'] == 'gateway_credential_failed'
    assert path.read_text() == 'existing'


def test_foreign_dropin_not_removed_by_rollback(paths):
    module.DROPIN.write_text('[Service]\nRestart=always\n'); module.DROPIN.chmod(0o600)
    assert module.run('rollback')['completed_dropins'] == []
    assert module.DROPIN.exists() and not module.ARCHIVE.exists()


def test_unit_output_contains_neither_source_path_value_nor_fingerprint(paths, capsys):
    assert module.main(['install']) == 0
    out = capsys.readouterr().out
    assert str(paths) not in out and 'test-only-noncredential' not in out
    assert 'sha256' not in out and 'hash' not in out
    assert json.loads(out)['credential_path_printed'] is False


def test_postrename_fsync_failure_reports_completed_dropin(paths, monkeypatch):
    original = module._sync
    def fail(path):
        if path == module.DROPINS and module.DROPIN.exists(): raise OSError('private')
        original(path)
    monkeypatch.setattr(module, '_sync', fail)
    out = module.run('install')
    assert out['phase'] == 'directory_sync' and out['completed_dropins'] == ['credential']
    assert 'private' not in json.dumps(out)


@pytest.mark.parametrize('mode', [0o400, 0o600])
def test_systemd_runtime_loader_reads_only_named_guarded_credential(tmp_path, monkeypatch, mode):
    directory = tmp_path/'creds'; directory.mkdir(mode=0o700)
    file = directory/module.NAME; file.write_text('test-only-token\n'); file.chmod(mode)
    monkeypatch.setenv('CREDENTIALS_DIRECTORY', str(directory))
    captured = []
    def transport(token, *, timeout, budget_path=None):
        captured.append((token, timeout, budget_path))
        return object()
    monkeypatch.setattr('git_dialog.github.JsonHttp', transport)
    monkeypatch.setattr('git_dialog.github.GitHubDialog', lambda transport: ('dialog',transport))
    assert module.dialog_factory_from_systemd()[0] == 'dialog'
    assert captured == [('test-only-token', 5, '/home/kali/agent-dialog-beta/state/github-api-cache.p361.sqlite')]


def test_no_systemd_variable_has_no_credential_fallback(monkeypatch):
    monkeypatch.delenv('CREDENTIALS_DIRECTORY', raising=False)
    with pytest.raises(DialogError, match='gateway_systemd_credential_missing'):
        module.dialog_factory_from_systemd()


@pytest.mark.parametrize('kind', ['symlink','worldreadable','multilink','invalid'])
def test_runtime_credential_rejects_unsafe_shapes(tmp_path, monkeypatch, kind):
    directory = tmp_path/'creds'; directory.mkdir(mode=0o700)
    file = directory/module.NAME; file.write_text('test-only-token'); file.chmod(0o600)
    if kind == 'symlink':
        other = directory/'other'; file.rename(other); file.symlink_to(other)
    elif kind == 'worldreadable': file.chmod(0o644)
    elif kind == 'multilink': os.link(file, directory/'other')
    else: file.write_text('two tokens')
    monkeypatch.setenv('CREDENTIALS_DIRECTORY', str(directory))
    with pytest.raises(DialogError): module.dialog_factory_from_systemd()
