"""Small extension of the hash-pinned 35G gate's RPC decision."""
import fcntl
import os
import re
import stat
from pathlib import Path

ROOT = Path('/home/kali/hermes-pg-stage35v')
EVENTS = ROOT / 'denials.tsv'
# Read-only discovery operations whose implementations can be verified by the trial.
REVIEWED = frozenset({'server/discover', 'skills/list'})
# Additional public identifiers can be named in a shareable report without authorizing them.
PUBLIC = REVIEWED | frozenset('skills/get resources/templates/list resources/read prompts/get '
                              'logging/setLevel completion/complete notifications/progress '
                              'notifications/message notifications/roots/list_changed '
                              'notifications/elicitation/complete tasks/list tasks/get tasks/result '
                              'tasks/cancel tasks/subscribe roots/list sampling/createMessage '
                              'elicitation/create'.split())
LABEL = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,31}(?:[./][A-Za-z][A-Za-z0-9_-]{0,31}){0,6}', re.ASCII)

def safe_label(method):
    # Keep one bounded identifier; never render arbitrary strings or control characters.
    if len(method) > 96 or not LABEL.fullmatch(method):
        return '[redacted]'
    return method

def write_denial(method):
    name = safe_label(method)
    fd = os.open(EVENTS, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'ab', buffering=0) as output:
        fcntl.flock(fd, fcntl.LOCK_EX)
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_nlink != 1
                or stat.S_IMODE(st.st_mode) != 0o600):
            raise OSError('unsafe evidence file')
        if st.st_size < 64000:
            output.write(('unknown_method_denied\t' + name + '\n').encode('ascii'))
        else:
            sentinel = b'capture_full\t[redacted]\n'
            if os.pread(fd, len(sentinel), st.st_size-len(sentinel)) != sentinel:
                output.write(sentinel)

def install(gate, extra, recorder=write_denial):
    if not set(extra) <= REVIEWED:
        raise ValueError('unreviewed extension')
    # This module instance belongs to the new service process. The on-disk guard remains pinned.
    original = gate.required_for_body
    gate.METHODS = gate.METHODS.union(extra)
    def checked(body):
        try:
            return original(body)
        except gate.AuthDenied as error:
            if error.args == ('unknown_method_denied',):
                # original() already checked the top-level object and the method type.
                import json
                try:
                    recorder(json.loads(body)['method'])
                except OSError:
                    print('STAGE35V_PRIVATE_EVIDENCE_UNAVAILABLE', flush=True)
            raise
    gate.required_for_body = checked

def denials(path=EVENTS):
    if not os.path.lexists(path):
        return []
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        fcntl.flock(fd, fcntl.LOCK_SH)
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_nlink != 1
                or stat.S_IMODE(st.st_mode) != 0o600 or st.st_size > 65000):
            raise ValueError('unsafe_evidence')
        rows = []
        for raw in stream.read(65001).decode('ascii').splitlines():
            kind, name = raw.split('\t')
            if kind not in ('unknown_method_denied', 'capture_full') or (
                    name != '[redacted]' and safe_label(name) != name):
                raise ValueError('invalid_evidence')
            rows.append(name)
        return rows

def reviewed_subset(rows):
    seen = frozenset(rows)
    if not seen or not seen <= REVIEWED:
        raise ValueError('exact_method_requires_review')
    return tuple(sorted(seen))
