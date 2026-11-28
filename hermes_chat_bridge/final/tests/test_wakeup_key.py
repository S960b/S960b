import os
import stat

import pytest

from git_dialog import DialogError, runner, wakeup_key
from git_dialog.service import initialize


def state(tmp_path):
    path = tmp_path / 'beta'
    initialize(path, 'fixture-password-1234')
    (path / 'runner-candidate.marker').touch(mode=0o600)
    return path


def test_explicit_random_separate_private_key(tmp_path):
    path = state(tmp_path)
    db = (path / 'panel.sqlite').read_bytes()
    result = wakeup_key.provision(path)
    assert result == {'status': 'wakeup_key_initialized', 'wakeup_enabled': False}
    key = wakeup_key.load(path)
    assert len(key) == 32 and key != b'fixture-password-1234'
    assert stat.S_IMODE((path / 'wakeup.key').stat().st_mode) == 0o600
    assert (path / 'panel.sqlite').read_bytes() == db


def test_no_overwrite_or_implicit_create(tmp_path):
    path = state(tmp_path)
    with pytest.raises(DialogError): wakeup_key.load(path)
    assert not (path / 'wakeup.key').exists()
    wakeup_key.provision(path); before = wakeup_key.load(path)
    with pytest.raises(DialogError, match='wakeup_key_already_exists'): wakeup_key.provision(path)
    assert wakeup_key.load(path) == before


@pytest.mark.parametrize('size', [0, 1, 31, 33, 4096])
def test_size_rejected(tmp_path, size):
    path = state(tmp_path)
    target = path / 'wakeup.key'
    target.touch(mode=0o600); target.write_bytes(b'x' * size)
    with pytest.raises(DialogError): wakeup_key.load(path)


@pytest.mark.parametrize('mode', [0o644, 0o400, 0o666])
def test_permissions_rejected_not_fixed(tmp_path, mode):
    path = state(tmp_path); wakeup_key.provision(path)
    target = path / 'wakeup.key'; target.chmod(mode)
    with pytest.raises(DialogError): wakeup_key.load(path)
    assert stat.S_IMODE(target.stat().st_mode) == mode


def test_symlink_and_hardlink_rejected(tmp_path):
    path = state(tmp_path)
    target = tmp_path / 'unrelated'; target.touch(mode=0o600); target.write_bytes(b'a' * 32)
    (path / 'wakeup.key').symlink_to(target)
    with pytest.raises(DialogError): wakeup_key.load(path)
    with pytest.raises(DialogError): wakeup_key.provision(path)
    assert target.read_bytes() == b'a' * 32
    (path / 'wakeup.key').unlink()
    os.link(target, path / 'wakeup.key')
    with pytest.raises(DialogError): wakeup_key.load(path)


def test_old_p1_state_no_key_created(tmp_path):
    path = tmp_path / 'p1'; initialize(path, 'fixture-password-1234')
    with pytest.raises(DialogError): wakeup_key.provision(path)
    assert not (path / 'wakeup.key').exists()


def test_directory_symlink_rejected(tmp_path):
    path = state(tmp_path)
    link = tmp_path / 'link'; link.symlink_to(path)
    with pytest.raises(DialogError): wakeup_key.provision(link)
    assert not (path / 'wakeup.key').exists()


def test_cli_explicit_key_init_never_starts_listener(tmp_path, monkeypatch, capsys):
    path = state(tmp_path)
    monkeypatch.setattr(runner, 'serve_candidate', lambda *a, **k: pytest.fail('listener'))
    monkeypatch.setattr(runner, '_interactive_secret', lambda *a: pytest.fail('secret prompt'))
    assert runner.main(['key-init', '--state-dir', str(path)]) == 0
    assert runner.main(['key-init', '--state-dir', str(path)]) == 1
    assert 'wakeup_key_initialized' in capsys.readouterr().out


def test_default_off_does_not_load_key(tmp_path, monkeypatch):
    path = state(tmp_path)
    monkeypatch.setattr(wakeup_key, 'load', lambda *a: pytest.fail('default key read'))
    monkeypatch.setattr(runner, 'serve_candidate', lambda host, port: None)
    assert runner.main(['serve', '--state-dir', str(path)]) == 0


def test_missing_key_no_prompt_or_listener(tmp_path, monkeypatch):
    path = state(tmp_path)
    monkeypatch.setattr(runner, 'serve_candidate', lambda *a, **k: pytest.fail('listener'))
    monkeypatch.setattr(runner, '_interactive_secret', lambda *a: pytest.fail('secret prompt'))
    assert runner.main(['serve', '--state-dir', str(path), '--enable-wakeup']) == 1
    assert not (path / 'wakeup.key').exists()
