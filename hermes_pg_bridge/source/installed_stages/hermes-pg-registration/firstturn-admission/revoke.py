#!/usr/bin/env python3
"""Explicit, durable revocation of first-turn admissions.

    revoke.py status
    revoke.py add <task_id>
    revoke.py remove <task_id>
    revoke.py global on|off
    revoke.py clear

The state file is read by auto_admit and by the managed Inbox receiver on every
tick, so a revocation survives plugin reloads and keeper restarts.
"""
import json
import os
import re
import secrets
import stat
import sys
from pathlib import Path

AUTH = Path('/home/kali/.config/bridge-pg-v2/first-turn-admission')
STATE = AUTH / 'revoked.json'
TASK = re.compile(r'^task_[0-9a-f]{32}$')


def private_dir():
    meta = AUTH.lstat()
    if not stat.S_ISDIR(meta.st_mode) or meta.st_uid != os.geteuid() or (meta.st_mode & 0o777) != 0o700:
        raise SystemExit('unsafe authority directory')


def load():
    private_dir()
    if not STATE.exists():
        return {'version': 1, 'global': False, 'tasks': []}
    meta = STATE.lstat()
    if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid()
            or meta.st_nlink != 1 or (meta.st_mode & 0o777) != 0o600):
        raise SystemExit('unsafe revocation file')
    doc = json.loads(STATE.read_text(encoding='utf8'))
    if set(doc) != {'version', 'global', 'tasks'} or doc['version'] != 1 or not isinstance(doc['tasks'], list):
        raise SystemExit('revocation file has an unexpected shape')
    return doc


def save(doc):
    doc['tasks'] = sorted(set(doc['tasks']))
    data = json.dumps(doc, sort_keys=True).encode()
    temporary = AUTH / ('.tmp-%s' % secrets.token_hex(8))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(temporary, STATE)
        directory = os.open(AUTH, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main(argv):
    if not argv:
        raise SystemExit(__doc__)
    command = argv[0]
    doc = load()
    if command == 'status':
        print(json.dumps(doc, sort_keys=True))
        return 0
    if command == 'add' and len(argv) == 2:
        if not TASK.fullmatch(argv[1]):
            raise SystemExit('invalid task id')
        doc['tasks'] = sorted(set(doc['tasks']) | {argv[1]})
        save(doc)
        print('revoked', argv[1])
        return 0
    if command == 'remove' and len(argv) == 2:
        doc['tasks'] = [x for x in doc['tasks'] if x != argv[1]]
        save(doc)
        print('allowed', argv[1])
        return 0
    if command == 'global' and len(argv) == 2 and argv[1] in ('on', 'off'):
        doc['global'] = argv[1] == 'on'
        save(doc)
        print('global', argv[1])
        return 0
    if command == 'clear':
        save({'version': 1, 'global': False, 'tasks': []})
        print('cleared')
        return 0
    raise SystemExit(__doc__)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
