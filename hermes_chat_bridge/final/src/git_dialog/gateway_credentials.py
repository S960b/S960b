"""Owner-approved systemd credential binding, not credential discovery.

Only install prompts privately for an ALREADY configured PAT source file. Its
path stays in the new private local drop-in, never stdout/argv/env/Git/DB.
The service reads only systemd's named runtime credential; no TTY or fallback.
"""
import argparse
import json
import os
from pathlib import Path
import re
import stat

from . import DialogError
from .native_install import _directory, _absent, _sync

BASE = Path('/home/kali/agent-dialog-beta')
DROPINS = Path('/home/kali/.config/systemd/user/hermes-bridge.service.d')
DROPIN = DROPINS / '92-agent-dialog-gateway-credential.conf'
STAGE = DROPINS / '.gateway-credential.p22-stage'
ARCHIVE = BASE / '92-agent-dialog-gateway-credential.p22-rollback.conf'
NAME = 'authority-git-pat'
PREFIX = '# git-dialog gateway credential p22\n[Service]\nLoadCredential=' + NAME + ':'
SAFE_PATH = re.compile(r'/[A-Za-z0-9_./-]+')


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_mode,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read_private(path, modes):
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or
                stat.S_IMODE(before.st_mode) not in modes or before.st_nlink != 1 or
                not 1 <= before.st_size <= 4096):
            raise DialogError('gateway_credential_rejected')
        raw = os.read(fd, 4097)
        if len(raw) != before.st_size or _identity(before) != _identity(os.fstat(fd)):
            raise DialogError('gateway_credential_rejected')
        if _identity(before) != _identity(Path(path).lstat()):
            raise DialogError('gateway_credential_rejected')
        return raw, _identity(before)
    except (OSError, ValueError, UnicodeError):
        raise DialogError('gateway_credential_rejected') from None
    finally:
        if fd is not None: os.close(fd)


def _token(raw):
    try:
        token = raw.decode('ascii').rstrip('\r\n')
        if not token or any(not 33 <= ord(c) <= 126 for c in token):
            raise ValueError()
        return token
    except (ValueError, UnicodeError):
        raise DialogError('gateway_credential_rejected') from None


def dialog_factory_from_systemd():
    # ONLY this documented service-manager variable, never enumerate env or
    # inspect another service's credentials. Its value is a directory, not PAT.
    value = os.environ.get('CREDENTIALS_DIRECTORY')
    if not value:
        raise DialogError('gateway_systemd_credential_missing')
    directory = Path(value)
    if not directory.is_absolute() or '..' in directory.parts:
        raise DialogError('gateway_credential_rejected')
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
            stat.S_IMODE(info.st_mode) not in (0o500, 0o700)):
        raise DialogError('gateway_credential_rejected')
    for parent in directory.parents:
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or (stat.S_IMODE(info.st_mode) & 0o022 and not (
                info.st_uid == 0 and stat.S_IMODE(info.st_mode) & stat.S_ISVTX))):
            raise DialogError('gateway_credential_rejected')
    raw, _ = _read_private(directory / NAME, (0o400, 0o600))
    token = _token(raw)
    from .github import GitHubDialog, JsonHttp
    dialog = GitHubDialog(JsonHttp(token, timeout=5, budget_path="/home/kali/agent-dialog-beta/state/github-api-cache.p361.sqlite"))
    del raw, token
    return dialog


def _source_path(value):
    # Reject quoting/specifier/newline injection instead of inventing escaping
    # or copying/renaming an existing credential to make its name fit.
    if (not isinstance(value, str) or not SAFE_PATH.fullmatch(value) or
            os.path.normpath(value) != value or '..' in Path(value).parts):
        raise DialogError('gateway_credential_source_rejected')
    return Path(value)


def _unit(raw):
    try:
        value = raw.decode('ascii')
        if not value.startswith(PREFIX) or not value.endswith('\n'):
            raise ValueError()
        _source_path(value[len(PREFIX):-1])
    except (ValueError, UnicodeError, DialogError):
        raise DialogError('gateway_credential_dropin_rejected') from None
    return raw


def _new_unit(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        os.fchmod(stream.fileno(), 0o600)  # ONLY new inode, never repair.
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    if _read_private(path, (0o600,))[0] != raw:
        raise DialogError('gateway_credential_dropin_rejected')
    _sync(path.parent)


def run(action):
    phase, completed = 'guards', []
    try:
        _directory(BASE, 0o700); _directory(DROPINS, 0o700)
        if action == 'rollback':
            raw, identity = _read_private(DROPIN, (0o600,)); _unit(raw)
            _absent(ARCHIVE)
            phase = 'dropin_recheck'
            if _read_private(DROPIN, (0o600,))[1] != identity:
                raise DialogError('gateway_credential_dropin_changed')
            phase = 'archive_dropin'
            os.replace(DROPIN, ARCHIVE); completed.append('credential_removed')
            phase = 'directory_sync'; _sync(DROPINS); _sync(BASE)
            status = 'gateway_credential_restored'
        elif action in ('check', 'install'):
            _absent(DROPIN, STAGE, ARCHIVE)
            status = 'gateway_credential_checked'
            if action == 'install':
                from .service import _interactive_secret
                phase = 'private_source_input'
                path = _source_path(_interactive_secret('Уже настроенный PAT-файл, локально и скрыто: '))
                phase = 'source_validation'
                raw, identity = _read_private(path, (0o600,)); token = _token(raw)
                del raw, token
                unit = (PREFIX + str(path) + '\n').encode('ascii')
                phase = 'new_dropin_stage'; _new_unit(STAGE, unit)
                phase = 'source_recheck'
                # Metadata only: the source value was already validated above.
                if _identity(path.lstat()) != identity:
                    raise DialogError('gateway_credential_source_changed')
                _absent(DROPIN)
                phase = 'publish_dropin'
                os.replace(STAGE, DROPIN); completed.append('credential')
                phase = 'directory_sync'; _sync(DROPINS)
                status = 'gateway_credential_installed'
        else:
            raise DialogError('gateway_credential_action_rejected')
        return {'status':status, 'completed_dropins':completed,
                'service_restarted':False, 'daemon_reloaded':False,
                'credential_value_printed':False, 'credential_path_printed':False}
    except Exception:
        return {'error':'gateway_credential_failed','action':action,'phase':phase,
                'completed_dropins':completed,'retry_authorized':False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check','install','rollback'))
    args = parser.parse_args(argv)
    result = run(args.action)
    print(json.dumps(result, sort_keys=True))
    return int('error' in result)


if __name__ == '__main__':
    raise SystemExit(main())
