#!/usr/bin/env python3
"""Stage 39: bounded, operator-initiated, renew-only session for ONE managed task.

No SDK, PostgreSQL, plugin config, dispatch or message mutation. A timer refreshes
existing capabilities only while an authenticated active session is valid. If a
capability disappears or fails validation, the session fails closed; it is never
silently re-armed. Stage37A is the independent revoker and watchdog.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import pwd
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path

HOME = Path('/home/kali')
ROOT = HOME / 'hermes-pg-stage39-operator-session'
SESSION = ROOT / 'session.json'
CFG = HOME / '.config/gigatool/opencode.json'
CAP = HOME / '.config/bridge-pg-v2'
PERMIT = CAP / 'managed-mcp-write-permit35c.json'
GATE = CAP / 'managed-writes-stage34d-armed'
LOCK = CAP / '.stage37a-revoke.lock'
WATCHDOG = HOME / 'hermes-pg-stage37a-permission-control/stage37a.py'
CANARY = HOME / 'hermes-pg-stage33i-ackfix/mt-pg-inbox-agent-stage33i.mjs'
CANDIDATE = HOME / 'hermes-pg-stage37b2c-claim-intent/mt-pg-inbox-managed-stage37b2c-unregistered.mjs'
LOCAL_STATE = HOME / '.local/state/mt-pg-inbox-managed-stage34e/state.json'
INTENTS = HOME / '.local/state/mt-pg-inbox-managed-stage34e/claim-intents37b2c'
TASK = 'task_b6c26aa18220412bafdbf32393f16568'
CFG_SHA = 'f46c3d4c2a46bd28d545ecd8a4c435e9cb6f0938723cdd106cbb58e6aa4d0bf5'
CANDIDATE_SHA_PREFIX = '06be5ea765f0'
WATCHDOG_SHA = '66cdc42d30cacf4f6ac8f038a11001a2fbac722bdd9c4848f89dbe4424625f07'
GATE_BYTES = b'ALLOW_MANAGED_WRITES_AFTER_EXCLUSIVE_SDK_REVIEW\n'
MAX_SESSION_SECONDS = 6 * 3600
PERMIT_LIFETIME_SECONDS = 3600
PERMIT_RENEW_IF_LEFT_SECONDS = 1800
GATE_RENEW_AFTER_SECONDS = 300
GATE_MAX_AGE_SECONDS = 1200
SESSION_KEYS = {'version', 'session_id', 'task_id', 'config_sha256', 'boot_id', 'created_unix', 'deadline_unix', 'status', 'reason', 'last_renew_unix'}
PERMIT_KEYS = {'version', 'expires_unix', 'config_sha256', 'task_ids', 'allow_create', 'static_tunnel_verified'}

class Stop(Exception):
    pass

def require(ok, reason):
    if not ok:
        raise Stop('STAGE39_STOP_' + reason)

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def owner():
    require(os.getuid() == os.geteuid() != 0 and pwd.getpwuid(os.getuid()).pw_name == 'kali' and Path.home() == HOME and os.environ.get('HOME') == str(HOME), 'OWNER')

def trusted_dir(path):
    st = path.lstat()
    require(stat.S_ISDIR(st.st_mode) and not path.is_symlink() and st.st_uid == os.geteuid() and not st.st_mode & 0o022, 'DIRECTORY_' + path.name)

def read_private(path, limit=250000, private=False):
    st = path.lstat()
    require(stat.S_ISREG(st.st_mode) and not path.is_symlink() and st.st_uid == os.geteuid() and st.st_nlink == 1 and (not st.st_mode & (0o077 if private else 0o022)) and 0 < st.st_size <= limit, 'FILE_UNTRUSTED_' + path.name)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        fst = os.fstat(fd)
        require((st.st_dev, st.st_ino, st.st_size) == (fst.st_dev, fst.st_ino, fst.st_size), 'FILE_RACE_' + path.name)
        raw = os.read(fd, limit + 1)
        require(len(raw) == st.st_size, 'FILE_CHANGED_' + path.name)
        return raw
    finally:
        os.close(fd)

def exists(path):
    return path.exists() or path.is_symlink()

def sync_directory(folder):
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)

def atomic_private(path, data, *, exclusive=False):
    require(isinstance(data, bytes) and len(data) <= 4096 and data, 'BAD_ATOMIC_BLOB')
    if exclusive:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally: os.close(fd)
    else:
        fd, temporary = tempfile.mkstemp(prefix='.stage39-', dir=path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'wb') as f:
                fd = -1
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, path)
        finally:
            if fd >= 0: os.close(fd)
            if os.path.lexists(temporary): os.unlink(temporary)
    sync_directory(path.parent)

def boot_id():
    val = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    require(len(val) == 36, 'BOOT_ID')
    return val

def parse_session(blob):
    obj = json.loads(blob)
    require(isinstance(obj, dict) and set(obj) == SESSION_KEYS and type(obj['version']) is int and obj['version'] == 1, 'SESSION_SCHEMA')
    require(isinstance(obj['session_id'], str) and len(obj['session_id']) == 32 and all(c in '0123456789abcdef' for c in obj['session_id']), 'SESSION_ID')
    require(obj['task_id'] == TASK and obj['config_sha256'] == CFG_SHA, 'SESSION_SCOPE')
    require(isinstance(obj['boot_id'], str) and len(obj['boot_id']) == 36, 'SESSION_BOOT')
    require(all(type(obj[k]) is int and obj[k] > 0 for k in ('created_unix', 'deadline_unix', 'last_renew_unix')), 'SESSION_TIMES')
    require(0 < obj['deadline_unix'] - obj['created_unix'] <= MAX_SESSION_SECONDS, 'SESSION_HORIZON')
    require(obj['status'] in ('arming', 'active', 'closed', 'fault'), 'SESSION_STATUS')
    require(isinstance(obj['reason'], str) and len(obj['reason']) < 120, 'SESSION_REASON')
    return obj

def session_read():
    if not exists(SESSION): return None
    return parse_session(read_private(SESSION, 4096, private=True))

def session_write(doc):
    parse_session(json.dumps(doc).encode())
    atomic_private(SESSION, (json.dumps(doc, sort_keys=True, separators=(',', ':')) + '\n').encode(), exclusive=False)

def make_permit(now, deadline):
    expiry = min(now + PERMIT_LIFETIME_SECONDS, deadline)
    require(expiry > now, 'PERMIT_DEADLINE')
    return {'version': 1, 'expires_unix': expiry, 'config_sha256': CFG_SHA, 'task_ids': [TASK], 'allow_create': False, 'static_tunnel_verified': True}

def permit_blob(doc):
    return (json.dumps(doc, sort_keys=True, separators=(',', ':')) + '\n').encode()

def validate_permit(now, deadline):
    obj = json.loads(read_private(PERMIT, 4096, private=True))
    require(isinstance(obj, dict) and set(obj) == PERMIT_KEYS, 'PERMIT_SCHEMA')
    require(type(obj['version']) is int and obj['version'] == 1 and type(obj['expires_unix']) is int, 'PERMIT_TYPES')
    require(obj['config_sha256'] == CFG_SHA and obj['task_ids'] == [TASK] and obj['allow_create'] is False and obj['static_tunnel_verified'] is True, 'PERMIT_SCOPE')
    require(now < obj['expires_unix'] <= min(now + PERMIT_LIFETIME_SECONDS + 60, deadline), 'PERMIT_EXPIRED_OR_EXCESSIVE')
    return obj

def validate_gate(now):
    raw = read_private(GATE, 128, private=True)
    require(raw == GATE_BYTES, 'GATE_VALUE')
    st = GATE.lstat()
    require(-60 <= now - st.st_mtime < GATE_MAX_AGE_SECONDS, 'GATE_EXPIRED')
    return st.st_mtime

def check_config():
    raw = read_private(CFG, 2097152)
    require(digest(raw) == CFG_SHA, 'CONFIG_SHA_CHANGED')
    config = json.loads(raw)
    require(isinstance(config, dict) and isinstance(config.get('plugin'), list) and len(config['plugin']) == 8, 'CONFIG_PLUGINS')
    entries = config['plugin']
    names = [e if isinstance(e, str) else e[0] if isinstance(e, list) and len(e) == 2 and isinstance(e[0], str) and isinstance(e[1], dict) else None for e in entries]
    require(len(set(names)) == 8 and names.count(str(CANARY)) == 1 and names.count(str(CANDIDATE)) == 1 and names.index(str(CANDIDATE)) == 7, 'CONFIG_PATHS')
    require(entries[7][1] == {'dry_run': False, 'tick_ms': 30000, 'task_ids': [TASK]}, 'CONFIG_SCOPE')
    require(digest(read_private(CANDIDATE, 250000)).startswith(CANDIDATE_SHA_PREFIX), 'PLUGIN_SOURCE_CHANGED')
    require(digest(read_private(WATCHDOG, 250000)) == WATCHDOG_SHA, 'WATCHDOG_CODE_CHANGED')

def watchdog_running():
    r = subprocess.run(['systemctl', '--user', 'is-active', '--quiet', 'hermes-pg-stage37a-permit-watchdog.timer'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
    require(r.returncode == 0, 'WATCHDOG_INACTIVE')

def health():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for u in ('http://127.0.0.1:18777/ping', 'http://127.0.0.1:18778/readyz'):
        try:
            with opener.open(u, timeout=3) as resp: good = resp.status == 200
        except Exception: good = False
        require(good, 'MCP_OR_TUNNEL_UNHEALTHY')

def preflight_start():
    owner()
    for p in (HOME, HOME / '.config', CAP, CFG.parent, ROOT): trusted_dir(p)
    check_config()
    watchdog_running()
    require(subprocess.run(['systemctl', '--user', 'is-active', '--quiet', 'hermes-pg-stage39-keeper.timer'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8).returncode == 0, 'KEEPER_TIMER_INACTIVE')
    health()
    state = json.loads(read_private(LOCAL_STATE, 65536))
    require(isinstance(state, dict) and state.get('inflight') == {} and isinstance(state.get('seen'), dict) and state['seen'].get(TASK, 0) >= 1, 'LOCAL_CURSOR_INFLIGHT')
    if exists(INTENTS):
        trusted_dir(INTENTS)
        require(not any(INTENTS.iterdir()), 'CLAIM_INTENT_PRESENT')
    require(not exists(PERMIT) and not exists(GATE), 'CAPABILITIES_ALREADY_PRESENT')

@contextmanager
def guard_lock():
    trusted_dir(CAP)
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        st = os.fstat(fd)
        require(stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid() and st.st_nlink == 1 and not st.st_mode & 0o077, 'WATCHDOG_LOCK')
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

def quarantine_one(path):
    if not exists(path): return False
    st = path.lstat()
    require(not stat.S_ISDIR(st.st_mode), 'CAPABILITY_PATH_DIRECTORY')
    dest = CAP / ('.revoked-stage39-' + path.name + '-' + str(time.time_ns()) + '-' + str(os.getpid()) + '.bak')
    require(not exists(dest), 'QUARANTINE_COLLISION')
    os.rename(path, dest)
    sync_directory(CAP)
    require(not exists(path), 'QUARANTINE_INCOMPLETE')
    return True

def revoke_locked():
    # Same order as Stage37A; no config/DB dependency.
    a = quarantine_one(GATE)
    b = quarantine_one(PERMIT)
    require(not exists(GATE) and not exists(PERMIT), 'REVOKE_UNCONFIRMED')
    return a, b

def closed(doc, reason):
    doc = dict(doc)
    doc['status'] = 'fault' if reason != 'OPERATOR_CLOSED' and reason != 'HARD_DEADLINE' else 'closed'
    doc['reason'] = reason[:119]
    session_write(doc)
    return doc

def start(now=None):
    now = int(time.time()) if now is None else int(now)
    preflight_start()
    with guard_lock():
        previous = session_read()
        require(previous is None or previous['status'] in ('closed', 'fault'), 'SESSION_ALREADY_ACTIVE')
        require(not exists(GATE) and not exists(PERMIT), 'CAPABILITIES_ALREADY_PRESENT')
        current = {'version': 1, 'session_id': uuid.uuid4().hex, 'task_id': TASK, 'config_sha256': CFG_SHA,
                   'boot_id': boot_id(), 'created_unix': now, 'deadline_unix': now + MAX_SESSION_SECONDS,
                   'status': 'arming', 'reason': '', 'last_renew_unix': now}
        session_write(current)
        try:
            atomic_private(GATE, GATE_BYTES, exclusive=True)
            atomic_private(PERMIT, permit_blob(make_permit(now, current['deadline_unix'])), exclusive=True)
            validate_gate(now)
            validate_permit(now, current['deadline_unix'])
            current['status'] = 'active'
            session_write(current)
        except BaseException:
            revoke_locked()
            closed(current, 'START_INCOMPLETE')
            raise
    print('STAGE39_SESSION_STARTED_ONE_SCOPED_DIALOGUE')
    print('STAGE39_TASK_ID', TASK)
    print('STAGE39_SESSION_ID', current['session_id'])
    print('STAGE39_ABSOLUTE_DEADLINE_UNIX', current['deadline_unix'])
    print('STAGE39_GATE_RENEWED_BY_TIMER_BEFORE_20_MINUTES')
    print('STAGE39_PERMIT_RENEWED_BY_TIMER_BEFORE_60_MINUTES')
    print('STAGE39_NO_CREATE_NO_CONFIG_OR_DATABASE_CHANGE')
    print('STAGE39_NEXT_CHATGPT_CAN_SET_DISPATCH_AND_SEND_TASK')

def tick(now=None):
    now = int(time.time()) if now is None else int(now)
    with guard_lock():
        try:
            doc = session_read()
        except (Stop, OSError, ValueError, TypeError):
            revoke_locked()
            print('STAGE39_KEEPER_SESSION_INVALID_REVOKED_BOTH')
            return
        if doc is None or doc['status'] in ('closed', 'fault'):
            if exists(GATE) or exists(PERMIT):
                revoke_locked()
                print('STAGE39_KEEPER_INACTIVE_STRAY_CAPS_REVOKED')
            else:
                print('STAGE39_KEEPER_INACTIVE_NO_REARM')
            return
        if doc['status'] != 'active':
            revoke_locked()
            closed(doc, 'INCOMPLETE_START')
            print('STAGE39_KEEPER_CLOSED_INCOMPLETE_SESSION')
            return
        if now >= doc['deadline_unix'] or doc['boot_id'] != boot_id():
            revoke_locked()
            closed(doc, 'HARD_DEADLINE' if now >= doc['deadline_unix'] else 'BOOT_CHANGED')
            print('STAGE39_KEEPER_CLOSED_AT_BOUNDARY')
            return
        try:
            owner()
            for p in (HOME, HOME / '.config', CAP, CFG.parent, ROOT): trusted_dir(p)
            check_config()
            watchdog_running()
            validate_gate(now)
            perm = validate_permit(now, doc['deadline_unix'])
            # Never recreate a missing or revoked capability.
            age = now - GATE.stat().st_mtime
            if age >= GATE_RENEW_AFTER_SECONDS:
                # Revalidate immediately before altering the same inode.
                validate_gate(now)
                os.utime(GATE, ns=(now * 1_000_000_000, now * 1_000_000_000), follow_symlinks=False)
                with open(GATE, 'rb') as f: os.fsync(f.fileno())
                sync_directory(CAP)
            if perm['expires_unix'] - now <= PERMIT_RENEW_IF_LEFT_SECONDS:
                atomic_private(PERMIT, permit_blob(make_permit(now, doc['deadline_unix'])))
            doc['last_renew_unix'] = now
            session_write(doc)
            print('STAGE39_KEEPER_RETAINED_VALID_SESSION')
        except (Stop, OSError, ValueError, TypeError, subprocess.TimeoutExpired) as exc:
            reason = str(exc) if isinstance(exc, Stop) else type(exc).__name__
            revoke_locked()
            closed(doc, 'FAIL_CLOSED_'+reason[:104])
            print('STAGE39_KEEPER_FAULT_REVOKED_BOTH')
            print('STAGE39_KEEPER_REASON', reason[:104])

def stop():
    owner()
    with guard_lock():
        doc = session_read()
        a, b = revoke_locked()
        if doc is not None and doc['status'] not in ('closed', 'fault'):
            closed(doc, 'OPERATOR_CLOSED')
    print('STAGE39_OPERATOR_STOP_REVOKED_GATE', a)
    print('STAGE39_OPERATOR_STOP_REVOKED_PERMIT', b)
    print('STAGE39_ACTIVE_CAPABILITIES_ABSENT')

def status():
    owner()
    doc = session_read()
    print('STAGE39_SESSION_STATUS', doc['status'] if doc is not None else 'NOT_STARTED')
    if doc:
        print('STAGE39_SESSION_ID', doc['session_id'])
        print('STAGE39_SESSION_DEADLINE_UNIX', doc['deadline_unix'])
        print('STAGE39_SESSION_SECONDS_LEFT', max(0, doc['deadline_unix'] - int(time.time())))
        print('STAGE39_SESSION_REASON', doc['reason'] or 'NONE')
    print('STAGE39_MCP_PERMIT_PRESENT', exists(PERMIT))
    print('STAGE39_AGENT_GATE_PRESENT', exists(GATE))
    if exists(PERMIT) and doc:
        try:
            p = validate_permit(int(time.time()), doc['deadline_unix'])
            print('STAGE39_MCP_PERMIT_VALID_UNTIL_UNIX', p['expires_unix'])
        except (Stop, OSError, ValueError) as e: print('STAGE39_MCP_PERMIT_VALID', False)
    print('STAGE39_STATUS_NO_DATABASE_OR_CONFIG_MUTATION')

def main():
    try:
        require(len(sys.argv) == 2 and sys.argv[1] in ('start', 'tick', 'stop', 'status'), 'USAGE')
        {'start': start, 'tick': tick, 'stop': stop, 'status': status}[sys.argv[1]]()
    except Stop as exc:
        print(str(exc), file=sys.stderr, flush=True)
        raise SystemExit(2)
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print('STAGE39_STOP_' + type(exc).__name__, file=sys.stderr, flush=True)
        raise SystemExit(2)

if __name__ == '__main__': main()
