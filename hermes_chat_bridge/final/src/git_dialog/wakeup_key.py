"""Explicit dedicated beta HMAC key provisioning; no discovery or secret output."""
import os
import secrets
import stat

from . import DialogError
from .service import _private_dir

NAME = "wakeup.key"


def _regular(fd, *, size=None):
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
            stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or
            (size is not None and info.st_size != size)):
        raise DialogError("wakeup_key_file_rejected")


def _directory(state_dir):
    directory = _private_dir(state_dir)
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise DialogError("wakeup_key_directory_rejected")
        marker = os.open("runner-candidate.marker", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=fd)
        try: _regular(marker, size=0)
        finally: os.close(marker)
        return fd
    except BaseException:
        os.close(fd)
        raise


def provision(state_dir):
    """One explicit operation, new random key only. Never rotate/overwrite/reuse."""
    directory = fd = None
    try:
        directory = _directory(state_dir)
        fd = os.open(NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        _regular(fd, size=0)
        key = secrets.token_bytes(32)
        written = 0
        while written < len(key):
            count = os.write(fd, key[written:])
            if count <= 0: raise DialogError("wakeup_key_write_failed")
            written += count
        os.fsync(fd)
        os.fsync(directory)
        # No bytes/path/fingerprint returned; incomplete files are not retried/deleted.
        return {"status": "wakeup_key_initialized", "wakeup_enabled": False}
    except FileExistsError:
        raise DialogError("wakeup_key_already_exists") from None
    except OSError:
        raise DialogError("wakeup_key_io_failed") from None
    finally:
        if fd is not None: os.close(fd)
        if directory is not None: os.close(directory)


def load(state_dir):
    """Fixed dedicated file only, no argv/env/path discovery or secret reuse."""
    directory = fd = None
    try:
        directory = _directory(state_dir)
        fd = os.open(NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        _regular(fd, size=32)
        key = os.read(fd, 33)
        if len(key) != 32: raise DialogError("wakeup_key_file_rejected")
        return key
    except OSError:
        raise DialogError("wakeup_key_io_failed") from None
    finally:
        if fd is not None: os.close(fd)
        if directory is not None: os.close(directory)
