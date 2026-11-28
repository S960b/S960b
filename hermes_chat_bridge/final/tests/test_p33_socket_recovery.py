"""P33 regression checks: local sockets only; no production paths or services."""
import errno
import os
from pathlib import Path
import socket
import stat
from types import SimpleNamespace

import pytest

from git_dialog import DialogError
from git_dialog import socket_recovery as recovery
from git_dialog.unix_authority import UnixServer
from test_unix_authority import runtime  # shared tmp-only fake Git/SQLite runtime


def endpoints(tmp_path):
    return {"chatgpt": tmp_path / "cg.sock", "mt": tmp_path / "mt.sock"}


def make_stale(path):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.bind(str(path))
    finally:
        sock.close()  # leaves an orphaned filesystem socket
    path.chmod(0o600)
    return path.lstat().st_ino


def make_live(path):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(path))
    path.chmod(0o600)
    sock.listen(1)
    return sock


def test_clean_start_and_shutdown_preserves_empty_lock_inode(tmp_path, runtime):
    paths = endpoints(tmp_path)
    server = UnixServer(runtime[3], paths)
    try:
        for path in paths.values():
            assert stat.S_ISSOCK(path.lstat().st_mode)
            assert stat.S_IMODE(path.lstat().st_mode) == 0o600
        lock = tmp_path / "authority-start.lock"
        info = lock.lstat()
        assert stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
        assert info.st_size == 0
        original = info.st_ino
    finally:
        server.close()
    assert all(not p.exists() for p in paths.values())
    assert lock.lstat().st_ino == original
    again = UnixServer(runtime[3], paths)
    again.close()


def test_stale_sockets_replaced_then_recovered_after_second_crash(tmp_path, runtime, monkeypatch):
    paths = endpoints(tmp_path)
    first_inodes = {k: make_stale(v) for k, v in paths.items()}
    # Project conftest forbids all connect_ex calls, including AF_UNIX.
    # Only this dependency is simulated; the real lock and bind run in tmp_path.
    probes = []
    def no_listener(path):
        probes.append(path)
        return errno.ECONNREFUSED
    monkeypatch.setattr(recovery, "_probe_listener", no_listener)
    server = UnixServer(runtime[3], paths)
    assert probes == list(paths.values())
    try:
        assert all(v.lstat().st_ino != first_inodes[k] for k, v in paths.items())
        assert all(stat.S_ISSOCK(v.lstat().st_mode) for v in paths.values())
    finally:
        server.close()
    assert all(not v.exists() for v in paths.values())


def test_live_socket_owned_by_same_uid_must_not_be_removed(tmp_path, runtime, monkeypatch):
    paths = endpoints(tmp_path)
    live = make_live(paths["chatgpt"])
    monkeypatch.setattr(recovery, "_probe_listener", lambda _: 0)
    inode = paths["chatgpt"].lstat().st_ino
    try:
        with pytest.raises(DialogError, match="authority_socket_busy"):
            UnixServer(runtime[3], paths)
        assert paths["chatgpt"].lstat().st_ino == inode
        assert not paths["mt"].exists()
    finally:
        live.close()
        paths["chatgpt"].unlink()


def test_second_server_cannot_start_while_first_is_alive(tmp_path, runtime):
    paths = endpoints(tmp_path)
    first = UnixServer(runtime[3], paths)
    original = {k: p.lstat().st_ino for k, p in paths.items()}
    try:
        with pytest.raises(DialogError, match="authority_start_locked"):
            UnixServer(runtime[3], paths)
        assert {k: p.lstat().st_ino for k, p in paths.items()} == original
    finally:
        first.close()
    second = UnixServer(runtime[3], paths)
    second.close()


@pytest.mark.parametrize("foreign", ["regular", "symlink", "mode"])
def test_foreign_or_unsafe_socket_is_not_removed(tmp_path, runtime, foreign):
    paths = endpoints(tmp_path)
    target = tmp_path / "target"
    if foreign == "regular":
        paths["chatgpt"].write_text("private")
        paths["chatgpt"].chmod(0o600)
    elif foreign == "symlink":
        target.write_text("do not touch")
        paths["chatgpt"].symlink_to(target)
    else:
        make_stale(paths["chatgpt"])
        paths["chatgpt"].chmod(0o644)
    node = paths["chatgpt"].lstat()
    with pytest.raises(DialogError, match="authority_socket_exists_or_rejected"):
        UnixServer(runtime[3], paths)
    assert paths["chatgpt"].lstat().st_ino == node.st_ino
    assert not paths["mt"].exists()
    if foreign == "symlink":
        assert target.read_text() == "do not touch"


def test_invalid_other_endpoint_prevents_partial_unlink(tmp_path, runtime, monkeypatch):
    paths = endpoints(tmp_path)
    inode = make_stale(paths["chatgpt"])
    monkeypatch.setattr(recovery, "_probe_listener", lambda _: errno.ECONNREFUSED)
    paths["mt"].write_bytes(b"foreign")
    paths["mt"].chmod(0o600)
    with pytest.raises(DialogError, match="authority_socket_exists_or_rejected"):
        UnixServer(runtime[3], paths)
    assert paths["chatgpt"].lstat().st_ino == inode
    assert paths["mt"].read_bytes() == b"foreign"


def test_suspicious_lock_inode_or_mode_is_never_adopted(tmp_path, runtime):
    paths = endpoints(tmp_path)
    lock = tmp_path / "authority-start.lock"
    lock.write_bytes(b"")
    lock.chmod(0o644)
    with pytest.raises(DialogError, match="authority_start_lock_rejected"):
        UnixServer(runtime[3], paths)
    assert stat.S_IMODE(lock.lstat().st_mode) == 0o644
    assert all(not p.exists() for p in paths.values())


def test_lock_symlink_rejected_without_following(tmp_path, runtime):
    paths = endpoints(tmp_path)
    target = tmp_path / "target"
    target.write_text("protected")
    (tmp_path / "authority-start.lock").symlink_to(target)
    with pytest.raises(OSError):
        UnixServer(runtime[3], paths)
    assert target.read_text() == "protected"


def test_path_replaced_during_probe_refuses_without_unlink(tmp_path, runtime, monkeypatch):
    paths = endpoints(tmp_path)
    inode = make_stale(paths["chatgpt"])
    monkeypatch.setattr(recovery, "_probe_listener", lambda _: errno.ECONNREFUSED)
    real = recovery._socket_info
    calls = 0

    def changed(path):
        nonlocal calls
        info = real(path)
        if path == paths["chatgpt"] and info is not None:
            calls += 1
            if calls == 2:
                return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino + 1,
                                       st_mode=info.st_mode, st_uid=info.st_uid,
                                       st_nlink=info.st_nlink, st_size=info.st_size)
        return info
    monkeypatch.setattr(recovery, "_socket_info", changed)
    with pytest.raises(DialogError, match="authority_socket_probe_unconfirmed"):
        UnixServer(runtime[3], paths)
    assert paths["chatgpt"].lstat().st_ino == inode


def test_uncertain_connect_never_removes_socket(tmp_path, runtime, monkeypatch):
    paths = endpoints(tmp_path)
    inode = make_stale(paths["chatgpt"])
    monkeypatch.setattr(recovery, "_probe_listener", lambda _: errno.EACCES)
    with pytest.raises(DialogError, match="authority_socket_probe_unconfirmed"):
        UnixServer(runtime[3], paths)
    assert paths["chatgpt"].lstat().st_ino == inode


def test_long_unix_socket_path_refused_without_creating_socket(tmp_path, runtime):
    long_path = tmp_path / ("x" * 95 + ".sock")
    paths = {"chatgpt": long_path, "mt": tmp_path / "mt.sock"}
    assert len(os.fsencode(long_path)) >= 108
    with pytest.raises(DialogError, match="authority_socket_exists_or_rejected"):
        UnixServer(runtime[3], paths)
    assert not long_path.exists()


def test_startup_failure_code_is_static_and_secret_free(tmp_path, monkeypatch, capsys):
    from git_dialog import gateway_lifespan as module
    from test_gateway_lifespan import setup

    registry, appends, factory, _ = setup(tmp_path, monkeypatch)
    def fail(_):
        raise DialogError("authority_socket_busy", "SECRETS_NEVER_LOGGED")
    monkeypatch.setattr(module, "UnixServer", fail)
    host = module.NativeGatewayHost(factory, registry=registry, append_store=appends)
    with pytest.raises(DialogError) as caught:
        host.start(seconds=2)
    assert caught.value.code == "authority_socket_busy"
    assert "SECRETS_NEVER_LOGGED" not in str(caught.value)
    assert "authority_start_failure code=authority_socket_busy" in capsys.readouterr().err
    assert host.finished.wait(2)


def test_restart_unit_has_bounded_retries_and_no_auto_deployment():
    config = (Path(__file__).resolve().parents[1] /
              "deploy/93-p33-bridge-restart.conf").read_text()
    assert "[Unit]" in config and "StartLimitBurst=3" in config
    assert "StartLimitIntervalSec=300" in config
    assert "[Service]" in config and "Restart=on-failure" in config
    assert "RestartSec=5s" in config
