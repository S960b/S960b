"""Explicit fixed-target native bridge install/activation/rollback; never starts a service."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

ORIGINAL = 'ffbbe5b4252f16544ed9de09f4a715cff2cb4347199866c47ba4584eb726647f'
NATIVE = '4921809e5aa9e7a67f7bee77f355d58123b076507d9d8f54da6b67dd4451c585'
LIMIT = 512 * 1024
SOURCE = Path('/home/kali/S960b/research/hermes_chat_bridge/bridge_server.py')
BASE = Path('/home/kali/agent-dialog-beta')
STATE = BASE / 'state'
BACKUP = BASE / 'bridge-original.p11.py'
ARCHIVE = BASE / 'bridge-native.p11-rollback.py'
STAGE = SOURCE.parent / '.bridge-server.p11-stage'
RESTORE = SOURCE.parent / '.bridge-server.p11-restore'
MARKER = STATE / 'bridge-wakeup.enabled'
DISABLED = STATE / 'bridge-wakeup.p11-disabled'


def _directory(path, mode=None):
    # Reject symlink components too, not just the final directory.
    for part in (path, *path.parents):
        info = part.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('directory_rejected')
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
        raise ValueError('directory_rejected')
    if mode is not None and stat.S_IMODE(info.st_mode) != mode:
        raise ValueError('directory_rejected')


def _read(path, digest, mode):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) != mode or info.st_nlink != 1 or
                info.st_size > LIMIT):
            raise ValueError('file_rejected')
        chunks, remaining = [], LIMIT + 1
        while remaining:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b''.join(chunks)
        after = os.fstat(fd)
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size,
                                  value.st_mtime_ns, value.st_ctime_ns)
        if (len(raw) > LIMIT or identity(info) != identity(after) or
                hashlib.sha256(raw).hexdigest() != digest):
            raise ValueError('content_rejected')
        return raw, identity(info)
    finally:
        os.close(fd)


def _absent(*paths):
    for path in paths:
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        raise ValueError('destination_occupied')


def _sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _new(path, raw, mode):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        # Creation of OUR NEW inode, never chmod/repair of an existing file.
        os.fchmod(fd, mode)
        offset = 0
        while offset < len(raw):
            count = os.write(fd, raw[offset:])
            if count <= 0:
                raise OSError('short_write')
            offset += count
        os.fsync(fd)
    finally:
        os.close(fd)
    _sync(path.parent)


def _prerequisites():
    from .service import AuthAuthority
    from .wakeup_key import load
    AuthAuthority(STATE)
    load(STATE)  # Existing verifier/dedicated key only, never print/provision.


def run(action, candidate_path=None):
    """Serialized operator use only. Rechecks are NOT a filesystem CAS/sandbox.

    Failures preserve partial artifacts; never retry, repair or auto-rollback.
    Only install/rollback replace the fixed source. No unit/ExecStart/env reads.
    """
    phase = 'guards'
    try:
        _directory(SOURCE.parent)
        _directory(BASE, 0o700)
        _directory(STATE, 0o700)
        if action in ('check', 'install'):
            original, identity = _read(SOURCE, ORIGINAL, 0o644)
            native, _ = _read(Path(candidate_path), NATIVE, 0o600)
            _absent(BACKUP, ARCHIVE, STAGE, RESTORE, MARKER, DISABLED)
            if action == 'check':
                return {'status': 'native_install_checked', 'live_source_modified': False,
                        'wakeup_enabled': False}
            phase = 'backup'
            _new(BACKUP, original, 0o600)
            _read(BACKUP, ORIGINAL, 0o600)
            phase = 'stage'
            _new(STAGE, native, 0o644)
            _read(STAGE, NATIVE, 0o644)
            phase = 'replace'
            if _read(SOURCE, ORIGINAL, 0o644)[1] != identity:
                raise ValueError('source_changed')
            _absent(MARKER, DISABLED)
            os.replace(STAGE, SOURCE)
            _sync(SOURCE.parent)
            phase = 'verify'
            _read(SOURCE, NATIVE, 0o644)
            return {'status': 'native_installed', 'backup_verified': True,
                    'wakeup_enabled': False, 'service_restarted': False}
        if action == 'activate':
            _, identity = _read(SOURCE, NATIVE, 0o644)
            _read(BACKUP, ORIGINAL, 0o600)
            _absent(STAGE, RESTORE, ARCHIVE, MARKER, DISABLED)
            phase = 'prerequisites'
            _prerequisites()
            phase = 'activate'
            if _read(SOURCE, NATIVE, 0o644)[1] != identity:
                raise ValueError('source_changed')
            _new(MARKER, b'', 0o600)
            _read(MARKER, hashlib.sha256(b'').hexdigest(), 0o600)
            return {'status': 'native_activation_created', 'service_restarted': False}
        if action == 'deactivate':
            _read(SOURCE, NATIVE, 0o644)
            _read(BACKUP, ORIGINAL, 0o600)
            _, identity = _read(MARKER, hashlib.sha256(b'').hexdigest(), 0o600)
            _absent(DISABLED)
            phase = 'deactivate'
            # Retain our empty activation artifact, no overwrite of another file.
            os.link(MARKER, DISABLED, follow_symlinks=False)
            # link changes ctime/nlink; verify both names designate the opened inode.
            info = MARKER.lstat()
            saved = DISABLED.lstat()
            if ((info.st_dev, info.st_ino) != identity[:2] or
                    (saved.st_dev, saved.st_ino) != identity[:2] or info.st_nlink != 2):
                raise ValueError('marker_changed')
            MARKER.unlink()
            _sync(STATE)
            return {'status': 'native_activation_retained_disabled', 'service_restarted': False}
        if action == 'rollback':
            current, identity = _read(SOURCE, NATIVE, 0o644)
            original, _ = _read(BACKUP, ORIGINAL, 0o600)
            _absent(MARKER, STAGE, RESTORE, ARCHIVE)
            phase = 'archive'
            _new(ARCHIVE, current, 0o600)
            _read(ARCHIVE, NATIVE, 0o600)
            phase = 'restore_stage'
            _new(RESTORE, original, 0o644)
            _read(RESTORE, ORIGINAL, 0o644)
            phase = 'rollback'
            if _read(SOURCE, NATIVE, 0o644)[1] != identity:
                raise ValueError('source_changed')
            _absent(MARKER)
            os.replace(RESTORE, SOURCE)
            _sync(SOURCE.parent)
            phase = 'verify'
            _read(SOURCE, ORIGINAL, 0o644)
            return {'status': 'native_original_restored', 'backup_preserved': True,
                    'native_archive_preserved': True, 'service_restarted': False}
        raise ValueError('action_rejected')
    except Exception:
        # Never exception text/type/path/config/secret. Partial writes remain for review.
        return {'error': 'native_install_failed', 'phase': phase,
                'action': action if action in ('check', 'install', 'activate', 'deactivate', 'rollback') else 'invalid'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check', 'install', 'activate', 'deactivate', 'rollback'])
    parser.add_argument('--candidate', help='Reviewed nonsensitive native candidate, install/check only')
    args = parser.parse_args(argv)
    if (args.action in ('check', 'install')) != (args.candidate is not None):
        print('{"error":"native_install_options_rejected"}')
        return 1
    result = run(args.action, args.candidate)
    print(json.dumps(result))
    return int('error' in result)


if __name__ == '__main__':
    raise SystemExit(main())
