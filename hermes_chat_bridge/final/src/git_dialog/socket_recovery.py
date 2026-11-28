"""P33: conservative recovery of abandoned Unix authority socket paths.

Linux-only. The private 0700 parent directory is a trusted local owner boundary.
An advisory flock prevents cooperating instances from racing unlink/bind. It
does not protect against a malicious *same-UID* process bypassing the lock;
that actor already has full access to the authority's local files.
"""
import errno
import fcntl
import os
from pathlib import Path
import socket
import stat

from . import DialogError

_LOCK_FILE = "authority-start.lock"
_CONNECT_TIMEOUT = 0.2


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink)


def _socket_info(path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _valid_socket(info):
    return (stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_nlink == 1 and info.st_size == 0)


def _probe_listener(path):
    """Single AF_UNIX liveness probe; injectable in offline regression tests.

    The project-wide test guard forbids even local connect_ex. Isolated
    recovery tests substitute this helper, never weakening that guard.
    """
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(_CONNECT_TIMEOUT)
        return probe.connect_ex(str(path))
    finally:
        probe.close()


class SocketStartupGuard:
    """Hold the same flock until both endpoints have stopped and been closed.

    The lock path is NEVER unlinked, so inode substitution by two cooperating
    starters cannot split the lock. An old lock file is harmless after a crash:
    the kernel releases flock when the last owning file description closes.
    """

    def __init__(self, paths):
        paths = tuple(Path(p) for p in paths)
        parents = {p.parent for p in paths}
        if len(paths) != 2 or len(parents) != 1:
            raise DialogError("authority_socket_binding_rejected")
        self.paths = paths
        self.lock_path = next(iter(parents)) / _LOCK_FILE
        if self.lock_path in paths:
            raise DialogError("authority_socket_binding_rejected")
        self.fd = None

    def acquire(self):
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = None
        try:
            try:
                fd = os.open(self.lock_path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                fd = os.open(self.lock_path, flags)
            # The path must still refer to this exact open inode. Never
            # chmod/chown an unexpected file or follow a symlink.
            actual, named = os.fstat(fd), self.lock_path.lstat()
            if (_identity(actual) != _identity(named) or
                    not stat.S_ISREG(actual.st_mode) or
                    actual.st_uid != os.getuid() or
                    stat.S_IMODE(actual.st_mode) != 0o600 or
                    actual.st_nlink != 1 or actual.st_size != 0):
                raise DialogError("authority_start_lock_rejected")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES):
                    raise DialogError("authority_start_locked") from None
                raise
            self.fd = fd
        except BaseException:
            if fd is not None:
                os.close(fd)
            raise

    def recover(self):
        if self.fd is None:
            raise DialogError("authority_start_locked")
        # Validate both socket paths BEFORE touching either, including the
        # live-listener probe. If one is suspicious, leave BOTH untouched.
        stale = []
        for path in self.paths:
            if len(os.fsencode(path)) >= 108:
                raise DialogError("authority_socket_exists_or_rejected")
            info = _socket_info(path)
            if info is None:
                continue
            if not _valid_socket(info):
                raise DialogError("authority_socket_exists_or_rejected")
            try:
                result = _probe_listener(path)
            except (OSError, TimeoutError):
                raise DialogError("authority_socket_probe_unconfirmed") from None
            if result == 0:
                raise DialogError("authority_socket_busy")
            if result != errno.ECONNREFUSED:
                raise DialogError("authority_socket_probe_unconfirmed")
            # Verify identity again after connect_ex. A disappearing or replaced
            # pathname is a race, not evidence that it is safe to remove.
            after = _socket_info(path)
            if after is None or _identity(after) != _identity(info):
                raise DialogError("authority_socket_probe_unconfirmed")
            stale.append((path, info))

        for path, info in stale:
            # The owner-only parent and held flock exclude concurrent trusted
            # modifiers. Recheck immediately before unlink to catch unexpected
            # foreign replacement; never remove a symlink or regular file.
            current = _socket_info(path)
            if current is None or _identity(current) != _identity(info):
                raise DialogError("authority_socket_probe_unconfirmed")
            path.unlink()

    def release(self):
        if self.fd is not None:
            fd, self.fd = self.fd, None
            os.close(fd)
