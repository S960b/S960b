"""Per-dialogue capabilities for the managed MultiTool CLI."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat
from first_turn_scope import AdmissionDenied, admitted

LEGACY = 'task_b6c26aa18220412bafdbf32393f16568'
OWNER_SCOPE = Path('/home/kali/.config/bridge-pg-v2/first-turn-admission')
REVOKE_LOCK = OWNER_SCOPE.parent / '.stage37a-revoke.lock'

@contextmanager
def hold_stage39_lock(lock_path=REVOKE_LOCK):
    """Serialize with Stage39 start, renewal and revocation."""
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(lock_path,flags)
    except OSError:
        raise AdmissionDenied('stage39_revoke_lock_unavailable') from None
    try:
        meta = os.fstat(fd)
        if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid()
                or meta.st_nlink != 1 or stat.S_IMODE(meta.st_mode) != 0o600):
            raise AdmissionDenied('stage39_revoke_lock_untrusted')
        try:
            fcntl.flock(fd,fcntl.LOCK_SH|fcntl.LOCK_NB)
        except OSError:
            raise AdmissionDenied('stage39_revoke_lock_busy') from None
        yield
    finally:
        os.close(fd)

@contextmanager
def new_dialogue_write(operation, task_id, stage39_scope):
    """Hold both locks until the managed agent operation completes."""
    with hold_stage39_lock(REVOKE_LOCK):
        with admitted(OWNER_SCOPE,task_id,operation,lambda: stage39_scope('authorize',LEGACY)):
            yield
