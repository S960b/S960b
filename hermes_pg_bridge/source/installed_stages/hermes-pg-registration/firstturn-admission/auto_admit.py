"""Owner standing approval for a fresh ChatGPT dialogue's first turn.

Runs as the service owner (kali). Reads only local authority and the read-only
agent CLI. Writes one admission record per new dialogue, bound to the exact
first message that the receiver will later verify. Never enables dispatch,
never sends a message, never touches the database directly.
"""
import fcntl
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_admission import LEGACY                                    # noqa: E402
from first_turn_scope import EXCLUDED, HEX32, HEX64, MESSAGE_ID, TASK_ID  # noqa: E402
from reserve56 import Reservation                                     # noqa: E402
from wire56 import payload                                            # noqa: E402

HOME = Path('/home/kali')
AUTH = HOME / '.config/bridge-pg-v2/first-turn-admission'
SIGNER = HOME / '.config/bridge-task-sign56'
JOURNALS = SIGNER / 'first-turn-reservations'
SESSION = HOME / 'hermes-pg-stage39-operator-session/session.json'
LOG = HOME / '.local/state/mt-pg-inbox-managed-stage34e/first-turn-admit.log'
CLI = [str(HOME / 'bridge-pg-v2/.venv/bin/python'), '-B',
       str(HOME / 'hermes-pg-stage34d-write-adapter/managed_agent_cli34d.py')]
ENV = dict(os.environ, PYTHONPATH=str(HOME / 'bridge-pg-v2'))
FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK


def log(category):
    try:
        LOG.parent.mkdir(mode=0o700, exist_ok=True)
        with open(LOG, 'a', encoding='ascii') as stream:
            stream.write('%d %s\n' % (int(time.time()), category))
    except OSError:
        pass


def need(condition, code):
    if not condition:
        raise RuntimeError(code)


def private_read(path, limit):
    fd = os.open(path, FLAGS)
    try:
        meta = os.fstat(fd)
        need(meta.st_uid == os.geteuid() and (meta.st_mode & 0o777) == 0o600
             and meta.st_nlink == 1 and 0 <= meta.st_size <= limit, 'private_file')
        with os.fdopen(os.dup(fd), 'rb') as stream:
            data = stream.read(limit + 1)
        need(len(data) == meta.st_size, 'private_file_short')
        return data
    finally:
        os.close(fd)


def private_dir(path, create=False):
    if create:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
    meta = path.lstat()
    need(meta.st_uid == os.geteuid() and (meta.st_mode & 0o777) == 0o700, 'private_dir')


def publish(path, data):
    temporary = path.with_name('.tmp-%s' % secrets.token_hex(8))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def cli(*args):
    result = subprocess.run(CLI + list(args), env=ENV, cwd=str(HOME / 'bridge-pg-v2'),
                            capture_output=True, text=True, timeout=60)
    need(result.returncode == 0, 'cli_failed')
    lines = result.stdout.strip().splitlines()
    need(bool(lines), 'cli_empty')
    return json.loads(lines[-1])


def keeper_session():
    doc = json.loads(private_read(SESSION, 65536))
    need(doc.get('status') == 'active' and doc.get('task_id') == LEGACY, 'keeper_inactive')
    sid = doc.get('session_id')
    need(type(sid) is str and HEX32.fullmatch(sid), 'keeper_session_id')
    return sid


def iter_dialogs(cli_call, page=50, max_scan=2000):
    """Walk every page of the dispatch list instead of only the first one."""
    offset = 0
    scanned = 0
    while True:
        listing = cli_call('list', '--offset', str(offset), '--limit', str(page))
        dialogs = listing.get('dialogs') or []
        if not dialogs:
            return
        for dialog in dialogs:
            yield dialog
        scanned += len(dialogs)
        nxt = listing.get('next_offset')
        if nxt is None or type(nxt) is not int or nxt <= offset:
            if nxt is not None and (type(nxt) is not int or nxt <= offset):
                log('scan_cursor_stall')
            return
        if scanned >= max_scan:
            log('scan_cap_hit ' + str(scanned))
            return
        offset = nxt


def ensure_policy(sid):
    path = AUTH / 'policy.json'
    epoch = None
    if path.exists():
        doc = json.loads(private_read(path, 2048))
        need(set(doc) == {'version', 'epoch', 'session_id'} and doc['version'] == 1
             and type(doc['epoch']) is str and HEX32.fullmatch(doc['epoch']), 'policy_shape')
        epoch = doc['epoch']
        if doc['session_id'] == sid:
            return epoch
    record = {'version': 1, 'epoch': epoch or secrets.token_hex(16), 'session_id': sid}
    publish(path, json.dumps(record, sort_keys=True).encode())
    log('policy_renewed')
    return record['epoch']


def revocation():
    """Explicit, durable revocation: global flag and a list of task ids."""
    path = AUTH / 'revoked.json'
    if not path.exists():
        return {'global': False, 'tasks': frozenset()}
    doc = json.loads(private_read(path, 65536))
    need(set(doc) == {'version', 'global', 'tasks'} and doc['version'] == 1
         and type(doc['global']) is bool and isinstance(doc['tasks'], list)
         and all(type(x) is str and TASK_ID.fullmatch(x) for x in doc['tasks']),
         'revocation_shape')
    return {'global': doc['global'], 'tasks': frozenset(doc['tasks'])}


def private_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    pem = private_read(SIGNER / 'task-private.pem', 4096)
    key = load_pem_private_key(pem, password=None)
    need(isinstance(key, Ed25519PrivateKey), 'signer_key_type')
    return key


def verify_wire(public, tid, mid, wire):
    header, body = wire.split('\n', 1)
    parts = header.split(' ')
    need(len(parts) == 4 and parts[0] == 'HPG1', 'wire_header')
    seq, ver, signature = int(parts[1]), int(parts[2]), parts[3]
    need(1 <= seq <= 2 ** 53 - 1 and 0 <= ver <= 2 ** 53 - 1, 'wire_counters')
    raw_sig = __import__('base64').urlsafe_b64decode(signature + '=' * (-len(signature) % 4))
    public.verify(raw_sig, payload(tid, mid, seq, ver, body))
    return body


def ensure_journal(tid, key):
    private_dir(JOURNALS, create=True)
    path = JOURNALS / (tid + '.sqlite')
    journal = Reservation(path, key, tid)
    if not path.exists():
        journal.initialize()
        directory = os.open(JOURNALS, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return True
    fd = os.open(path, FLAGS)
    try:
        meta = os.fstat(fd)
        need(meta.st_uid == os.geteuid() and (meta.st_mode & 0o777) == 0o600
             and meta.st_nlink == 1 and meta.st_size > 0, 'journal_untrusted')
    finally:
        os.close(fd)
    with journal.tx() as sql:
        need(sql.execute('SELECT tid,pin FROM scope').fetchall() == [(tid, journal.pin)],
             'journal_scope')
    return False


def admit(sid, epoch, public, key, task_id):
    record_path = AUTH / (task_id + '.json')
    if record_path.exists():
        return 'present'
    page = cli('peek', '--task-id', task_id, '--limit', '2')
    messages = page.get('messages') or []
    last = page.get('seq')
    if last == 1:
        need(len(messages) == 1, 'not_first_message')
        first = messages[0]
        need(first.get('seq') == 1 and first.get('from') == 'chatgpt'
             and first.get('to') == 'mt' and first.get('kind') == 'task', 'first_message_shape')
    elif last == 2:
        # MT published seq=1; the first inbound ChatGPT message is seq=2.
        need(len(messages) == 2, 'not_first_message')
        prior, first = messages[1], messages[0]
        need(prior.get('seq') == 1 and prior.get('from') == 'mt'
             and prior.get('to') == 'chatgpt', 'mt_first_shape')
        need(first.get('seq') == 2 and first.get('from') == 'chatgpt'
             and first.get('to') == 'mt' and first.get('kind') == 'task', 'first_message_shape')
    else:
        need(False, 'not_first_message')
    first_seq = first.get('seq')
    mid = first.get('message_id')
    need(type(mid) is str and MESSAGE_ID.fullmatch(mid), 'message_id')
    full = cli('read', '--task-id', task_id, '--seq', str(first_seq), '--message-id', mid)
    need(full.get('seq') == first_seq and full.get('message_id') == mid
         and isinstance(full.get('body'), str), 'message_read')
    body = verify_wire(public, task_id, mid, full['body'])
    need(0 < len(body.encode('utf8')) <= 32768, 'body_size')
    created = ensure_journal(task_id, key)
    record = {'task_id': task_id, 'epoch': epoch, 'first_message_id': mid,
              'body_sha256': hashlib.sha256(body.encode('utf8')).hexdigest(), 'version': 0}
    need(HEX64.fullmatch(record['body_sha256']) is not None, 'body_hash')
    publish(record_path, json.dumps(record, sort_keys=True).encode())
    return 'admitted' if created else 'admitted_existing_journal'


def main():
    need(os.getuid() == os.geteuid() != 0, 'owner_identity')
    private_dir(AUTH, create=True)
    lock_path = AUTH / 'authority.lock'
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        meta = os.fstat(fd)
        need(meta.st_uid == os.geteuid() and (meta.st_mode & 0o777) == 0o600, 'lock_mode')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log('admit_busy')
            return 0
        revoked = revocation()
        if revoked['global']:
            log('revoked_global')
            return 0
        sid = keeper_session()
        epoch = ensure_policy(sid)
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        public = load_pem_public_key(private_read(SIGNER / 'task-public.pem', 4096))
        key = private_key()
        fcntl.flock(fd, fcntl.LOCK_UN)
        for dialog in iter_dialogs(cli):
            task_id = dialog.get('task_id')
            if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
                continue
            if task_id == LEGACY or task_id in EXCLUDED:
                continue
            if task_id in revoked['tasks']:
                log('admit_revoked ' + task_id)
                continue
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                log('admit_busy')
                return 0
            try:
                outcome = admit(sid, epoch, public, key, task_id)
            except Exception as exc:
                log('admit_skip ' + (str(exc) if str(exc).isidentifier() else type(exc).__name__)
                    + ' ' + task_id)
                continue
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
            log('admit_' + outcome + ' ' + task_id)
        log('tick_ok')
        return 0
    finally:
        os.close(fd)


if __name__ == '__main__':
    sys.exit(main())
