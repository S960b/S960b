"""Read an owner-issued, per-dialogue execution admission.

The existing Stage39 guard must be checked by the caller supplied here. This
module only reads local authority. Registration and SQL results cannot grant it.
Policy changes and revocations must take an exclusive lock on the same file.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat

TASK_ID = re.compile(r'task_[0-9a-f]{32}')
MESSAGE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}')
HEX32 = re.compile(r'[0-9a-f]{32}')
HEX64 = re.compile(r'[0-9a-f]{64}')
FILE_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
EXCLUDED = frozenset((
    'task_b6c26aa18220412bafdbf32393f16568',  # original Stage39 path
    'task_f2c64cfdf7894537b35a19a6def977c6',  # retired canary
))
OPS = frozenset(('send_first', 'send', 'claim', 'authorize', 'append', 'ack', 'ack_report'))

class AdmissionDenied(PermissionError):
    pass

@dataclass(frozen=True)
class FirstMessage:
    message_id: str
    body: str
    version: int

def _need(ok):
    if not ok:
        raise AdmissionDenied('dialogue_execution_admission_denied')

@contextmanager
def _open_file(directory, name, limit):
    fd = os.open(name, FILE_FLAGS, dir_fd=directory)
    try:
        meta = os.fstat(fd)
        _need(stat.S_ISREG(meta.st_mode) and meta.st_uid == os.geteuid()
              and meta.st_nlink == 1 and stat.S_IMODE(meta.st_mode) == 0o600
              and 0 <= meta.st_size <= limit)
        yield fd, meta
    finally:
        os.close(fd)

def _read(directory, name):
    with _open_file(directory, name, 2048) as (fd, meta):
        with os.fdopen(os.dup(fd), 'rb') as stream:
            raw = stream.read(2049)
        _need(len(raw) == meta.st_size)
        doc = json.loads(raw)
    _need(type(doc) is dict)
    return doc

@contextmanager
def admitted(root: Path, task_id: str, operation: str, stage39_guard, *,
             first: FirstMessage | None = None):
    """Hold the shared local lock across signing and send commit, or a check.

    stage39_guard returns the trusted active Stage39 session ID (32 hex chars),
    after validating the gate, permit, boot, configured scope and renewal.
    """
    _need(type(task_id) is str and TASK_ID.fullmatch(task_id)
          and task_id not in EXCLUDED and type(operation) is str and operation in OPS and (first is not None) == (operation == 'send_first'))
    authorized = False
    try:
        root_fd = os.open(root, FILE_FLAGS | os.O_DIRECTORY)
        try:
            meta = os.fstat(root_fd)
            _need(meta.st_uid == os.geteuid() and stat.S_IMODE(meta.st_mode) == 0o700)
            with _open_file(root_fd, 'authority.lock', 0) as (lock, _):
                fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
                policy = _read(root_fd, 'policy.json')
                _need(set(policy) == {'version', 'epoch', 'session_id'}
                      and type(policy['version']) is int and policy['version'] == 1
                      and type(policy['epoch']) is str and HEX32.fullmatch(policy['epoch'])
                      and type(policy['session_id']) is str and HEX32.fullmatch(policy['session_id']))
                record = _read(root_fd, task_id + '.json')
                _need(set(record) == {'task_id', 'epoch', 'first_message_id', 'body_sha256', 'version'}
                      and record['task_id'] == task_id and record['epoch'] == policy['epoch']
                      and type(record['first_message_id']) is str
                      and MESSAGE_ID.fullmatch(record['first_message_id'])
                      and type(record['body_sha256']) is str and HEX64.fullmatch(record['body_sha256'])
                      and type(record['version']) is int and 0 <= record['version'] <= 2**53-1)
                if first is not None:
                    _need(type(first.version) is int and first.version == record['version']
                          and first.message_id == record['first_message_id']
                          and type(first.body) is str and len(first.body) <= 32768)
                    raw = first.body.encode('utf8')
                    _need(0 < len(raw) <= 32768 and hashlib.sha256(raw).hexdigest() == record['body_sha256'])
                _need(stage39_guard() == policy['session_id'])
                authorized = True
                yield task_id
        finally:
            os.close(root_fd)
    except (OSError, ValueError, TypeError) as exc:
        if authorized or isinstance(exc, AdmissionDenied):
            raise
        raise AdmissionDenied('dialogue_execution_admission_unavailable') from exc
