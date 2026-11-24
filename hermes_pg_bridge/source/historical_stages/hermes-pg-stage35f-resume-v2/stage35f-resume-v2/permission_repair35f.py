#!/usr/bin/env python3
"""Stage35F preflight recovery: precisely two SHA-pinned mode-644 files -> 600.

No content modification, runtime write, service/systemd or tunnel operation.
"""
from __future__ import annotations
import hashlib
import os
import pwd
import stat
import sys
from pathlib import Path

D = Path('/home/kali/hermes-pg-stage35d-local-smoke/stage35d.py')
C = Path('/home/kali/hermes-pg-stage35c-static-guard/static_guard35c.py')
PINS = (
    (D, 'bbed81ef18b508dedf08aa90ca6cb55ceced797242ad80f1c8c567d1c7a0ccfb'),
    (C, '643216a27641b915651cb4c9685d81789af5b7dbf4a19f17741d6bfdae40d06c'),
)
F = Path('/home/kali/hermes-pg-stage35f-guard-probe')
F_STAGE_SHA = 'c6271a43cc1668655445c58c5737111aa6ca4d4d0c86a1314f4382c2aac92e54'

class Reject(Exception):
    pass

def ensure(ok, code):
    if not ok:
        raise Reject(code)

def open_pinned(path: Path, expected: str, uid: int):
    """Open a pinned file without following its last symlink; return held fd and mode."""
    path = Path(path)
    d = path.parent
    ds = d.lstat()
    ensure(stat.S_ISDIR(ds.st_mode) and ds.st_uid == uid and (ds.st_mode & 0o022) == 0,
           'DIR_OWNER_OR_PERMISSIONS_UNSAFE')
    dfd = os.open(d, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dfd)
    finally:
        os.close(dfd)
    try:
        s = os.fstat(fd)
        mode = stat.S_IMODE(s.st_mode)
        ensure(stat.S_ISREG(s.st_mode) and s.st_uid == uid and s.st_nlink == 1
               and 1 <= s.st_size <= 200000, 'FILE_TYPE_OWNER_LINKS_SIZE_UNSAFE')
        ensure(mode in (0o600, 0o644), 'UNEXPECTED_FILE_MODE')
        h = hashlib.sha256()
        while True:
            b = os.read(fd, 65536)
            if not b:
                break
            h.update(b)
        ensure(h.hexdigest() == expected, 'PINNED_CONTENT_CHANGED')
        os.lseek(fd, 0, os.SEEK_SET)
        return fd, mode
    except BaseException:
        os.close(fd)
        raise

def verify_and_repair(entries, uid, *, apply=False):
    """All files validated before changing any permissions; no directory chmod."""
    opened = []
    try:
        for p, expected in entries:
            fd, mode = open_pinned(p, expected, uid)
            opened.append((fd, mode))
        if apply:
            for fd, mode in opened:
                if mode == 0o644:
                    os.fchmod(fd, 0o600)
                ensure(stat.S_IMODE(os.fstat(fd).st_mode) == 0o600,
                       'MODE_REPAIR_NOT_PRIVATE')
        return [mode for _, mode in opened]
    finally:
        for fd, _ in opened:
            os.close(fd)

def check_original_35f(uid):
    ensure(F.is_dir() and not F.is_symlink(), '35F_TARGET_MISSING_OR_LINK')
    ffd, _ = open_pinned(F/'stage35f.py', F_STAGE_SHA, uid)
    os.close(ffd)
    ensure(not (F/'stage35f-once-attempt.json').exists() and
           not (F/'stage35f-once-attempt.json').is_symlink() and
           not (F/'stage35f-uvicorn-private.log').exists() and
           not (F/'stage35f-uvicorn-private.log').is_symlink(),
           '35F_ALREADY_STARTED_STOP_NO_REPLAY')

def main():
    ensure(os.getuid() == os.geteuid() != 0 and pwd.getpwuid(os.getuid()).pw_name == 'kali'
           and Path.home() == Path('/home/kali'), 'BAD_OPERATOR')
    ensure(sys.argv[1:] == ['apply'], 'INVALID_ARGUMENTS')
    check_original_35f(os.getuid())
    old = verify_and_repair(PINS, os.getuid(), apply=True)
    assert old == [0o600, 0o600] or all(x in (0o600, 0o644) for x in old)
    print('STAGE35F_RESUME_TWO_PINNED_SOURCES_MODE_600_ONLY_CONTENT_UNCHANGED', flush=True)
    print('STAGE35F_RESUME_35F_ONCE_MARKER_ABSENT_NO_REPLAY', flush=True)

if __name__ == '__main__':
    try:
        main()
    except (Reject, OSError) as e:
        code = str(e) if isinstance(e, Reject) else 'FILESYSTEM_OPERATION_FAILED'
        print('STAGE35F_RESUME_STOP_' + code, file=sys.stderr, flush=True)
        raise SystemExit(2)
