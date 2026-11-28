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
CFG_SHA = '9e98eeca933c447831e214212267219440392ce4e6a7ec8e3b8ffa3e26c0796b'
CANDIDATE_SHA256 = 'aae583255db7cca72b4090bb23fb65a91354d486287fe8cfa4291c611680d10f'
WATCHDOG_SHA = '3c08be36ce5dc7fc615429a9ce43840a85d863cef0516f39e134caf04e0304c2'
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

class WatchdogBusy(Stop):
    pass

def mono_us():
    return time.monotonic_ns() // 1000

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
    require(isinstance(obj, dict) and type(obj.get('version')) is int and obj['version'] in (1, 2), 'SESSION_SCHEMA')
    require(set(obj) == (SESSION_KEYS if obj['version']==1 else SESSION_KEYS | {'last_renew_mono_us'}), 'SESSION_SCHEMA')
    require(isinstance(obj['session_id'], str) and len(obj['session_id']) == 32 and all(c in '0123456789abcdef' for c in obj['session_id']), 'SESSION_ID')
    require(obj['task_id'] == TASK and obj['config_sha256'] == CFG_SHA, 'SESSION_SCOPE')
    require(isinstance(obj['boot_id'], str) and len(obj['boot_id']) == 36, 'SESSION_BOOT')
    require(all(type(obj[k]) is int and obj[k] > 0 for k in ('created_unix', 'last_renew_unix')), 'SESSION_TIMES')
    require(obj['last_renew_unix'] >= obj['created_unix'], 'SESSION_TIMES_ORDER')
    if obj['version']==1:
        require(type(obj['deadline_unix']) is int and 0 < obj['deadline_unix'] - obj['created_unix'] <= MAX_SESSION_SECONDS, 'SESSION_HORIZON')
    else:
        require(obj['deadline_unix'] is None and type(obj['last_renew_mono_us']) is int and obj['last_renew_mono_us']>0, 'CONTINUOUS_SCHEMA')
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
    expiry = now + PERMIT_LIFETIME_SECONDS if deadline is None else min(now + PERMIT_LIFETIME_SECONDS, deadline)
    require(expiry > now, 'PERMIT_DEADLINE')
    return {'version': 1, 'expires_unix': expiry, 'config_sha256': CFG_SHA, 'task_ids': [], 'allow_create': True, 'static_tunnel_verified': True}

def permit_blob(doc):
    return (json.dumps(doc, sort_keys=True, separators=(',', ':')) + '\n').encode()

def validate_permit(now, deadline):
    obj = json.loads(read_private(PERMIT, 4096, private=True))
    require(isinstance(obj, dict) and set(obj) == PERMIT_KEYS, 'PERMIT_SCHEMA')
    require(type(obj['version']) is int and obj['version'] == 1 and type(obj['expires_unix']) is int, 'PERMIT_TYPES')
    require(obj['config_sha256'] == CFG_SHA and obj['task_ids'] == [] and obj['allow_create'] is True and obj['static_tunnel_verified'] is True, 'PERMIT_SCOPE')
    limit = now + PERMIT_LIFETIME_SECONDS + 60
    if deadline is not None: limit = min(limit, deadline)
    require(now < obj['expires_unix'] <= limit, 'PERMIT_EXPIRED_OR_EXCESSIVE')
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
    require(isinstance(config, dict) and isinstance(config.get('plugin'), list) and len(config['plugin']) == 6, 'CONFIG_PLUGINS')
    entries = config['plugin']
    names = [e if isinstance(e, str) else e[0] if isinstance(e, list) and len(e) == 2 and isinstance(e[0], str) and isinstance(e[1], dict) else None for e in entries]
    require(len(set(names)) == 6 and names.count(str(CANARY)) == 0 and names.count(str(HOME / '.config/gigatool/plugins/mt-test-plugin.mjs')) == 0 and names.count(str(CANDIDATE)) == 1 and names.index(str(CANDIDATE)) == 5, 'CONFIG_PATHS')
    require(entries[5][1] == {'dry_run': False, 'tick_ms': 30000, 'task_ids': [TASK]}, 'CONFIG_SCOPE')
    require(digest(read_private(CANDIDATE, 250000)) == CANDIDATE_SHA256, 'PLUGIN_SOURCE_CHANGED')
    require(digest(read_private(Path('/home/kali/hermes-pg-stage57-owner-turn/owner_turn57.py'),250000)) == '5e472145458ee51ee08287cc907df24a1cc45b5a99d3b665d0edf87e50ba8678', "OWNER_TURN57_SOURCE_CHANGED")
    require(digest(read_private(Path('/home/kali/hermes-pg-stage57-owner-turn/coordinator57.py'),250000)) == '02bd3879d596bf66be5e0e35ba06d3210056eaf8fd9cd9b31f27681ae4180eea', "OWNER_TURN57_SOURCE_CHANGED")
    require(digest(read_private(Path('/home/kali/hermes-pg-stage57-owner-turn/idle57.py'),250000)) == 'd2ba07a343d140f50295cd266e01bcb2a3fceababd2d65947363d420ddacfb84', "OWNER_TURN57_SOURCE_CHANGED")
    require(digest(read_private(Path('/home/kali/hermes-pg-stage57-owner-turn/adapter57.py'),250000)) == '1d4285acb71113244b6a8417d92785634ed59b0893880342f8c87b4257e1b494', "OWNER_TURN57_SOURCE_CHANGED")
    require(digest(read_private(Path('/home/kali/hermes-pg-stage57-owner-turn/worker57.py'),250000)) == '76633e83912ae1bdfd096f2a849451b07e317ee8e088c1ab7b8db6f9e713e1ba', "OWNER_TURN57_SOURCE_CHANGED")
    require(digest(read_private(Path('/home/kali/bridge-pg-v2/bridge_pg/owner_http57.py'),250000)) == 'ea25b42fcf5c6ed9eae39334314a3c9dbf9c5a3f42dcfae1121c9f4efd43a637', "OWNER_TURN57_SOURCE_CHANGED")
    require(digest(read_private(Path('/home/kali/bridge-pg-v2/bridge_pg/taskboard_v2.py'),250000)) == 'b81d5306941e32c0c4d2a3faa8f82f1cbc39981ea5d024d1cd24d4c624a3681d', "OWNER_TURN57_SOURCE_CHANGED")
    require(digest(read_private(Path('/home/kali/bridge-pg-v2/bridge_pg/static/taskboard.js'),250000)) == '9175c98f40a1aa16be594929aaf707d172a512bc6429373a55623e9a8c9f3dbc', "OWNER_TURN57_SOURCE_CHANGED")
    require(digest(read_private(Path('/home/kali/bridge-pg-v2/bridge_pg/adapter64.py'),250000)) == '78e59f75d98952352ee4d4079ded26d4ef83ce6358817d5528904e795316547d', "OWNER_TURN57_SOURCE_CHANGED")
    require(digest(read_private(Path('/home/kali/hermes-pg-stage37b2c-claim-intent/owner_turn57.mjs'),250000)) == '8e6ed3a4c82a18dc68387b8ca663c418061b7bbcdadde4a0fee5d8d4593f47ae', "OWNER_TURN57_SOURCE_CHANGED")
    _owner57_dir = Path('/home/kali/.local/state/mt-pg-inbox-managed-stage34e/owner-turn57-revocations').lstat()
    require(stat.S_ISDIR(_owner57_dir.st_mode) and _owner57_dir.st_uid==os.geteuid() and stat.S_IMODE(_owner57_dir.st_mode)==0o700, "OWNER_TURN57_STATE_UNSAFE")
    _owner57_dir = Path('/home/kali/.local/state/mt-pg-inbox-managed-stage34e/owner-turn57-requests').lstat()
    require(stat.S_ISDIR(_owner57_dir.st_mode) and _owner57_dir.st_uid==os.geteuid() and stat.S_IMODE(_owner57_dir.st_mode)==0o700, "OWNER_TURN57_STATE_UNSAFE")
    require(digest(read_private(CANDIDATE.parent / 'signed_receive56.mjs', 250000)) == '85265a1e20ca0efad43ad8a7c827df178d058905e7561c086b50af67bc9644f9', 'SIGNED_HELPER_SOURCE_CHANGED')
    require(digest(read_private(CANDIDATE.parent / 'wire56.mjs', 250000)) == '8382681c811efd38efa9ac5fa4e9f51544f837e7ce7e57592ac640112f22dfbe', 'SIGNED_HELPER_SOURCE_CHANGED')
    require(digest(read_private(CANDIDATE.parent / 'intent37b2c.mjs', 250000)) == '338a92a6f6d94c4e062230db48b449610f2dedc2a969866783908aa2da5ece7f', 'SIGNED_HELPER_SOURCE_CHANGED')
    require(digest(read_private(WATCHDOG, 250000)) == WATCHDOG_SHA, 'WATCHDOG_CODE_CHANGED')

def watchdog_running():
    # F09: timer activity is insufficient. Inspect successful completed service.
    def props(unit, fields):
        p=subprocess.run(['systemctl','--user','show',unit,'--property='+fields,'--no-pager'],capture_output=True,text=True,timeout=8)
        require(p.returncode==0,'WATCHDOG_SHOW')
        d=dict(line.split('=',1) for line in p.stdout.splitlines() if '=' in line)
        require(all(k in d for k in fields.split(',')),'WATCHDOG_PROPERTY_MISSING')
        return d
    t=props('hermes-pg-stage37a-permit-watchdog.timer','LoadState,ActiveState,SubState,Result')
    require(t['LoadState']=='loaded' and t['ActiveState']=='active' and t['SubState'] in ('waiting','running') and t['Result']=='success','WATCHDOG_INACTIVE')
    p=props('hermes-pg-stage37a-permit-watchdog.service','LoadState,ActiveState,SubState,Result,ExecMainCode,ExecMainStatus,ExecMainStartTimestampMonotonic,ExecMainExitTimestampMonotonic')
    require(p['LoadState']=='loaded','WATCHDOG_SERVICE_MISSING')
    now=mono_us()
    start=int(p['ExecMainStartTimestampMonotonic']); end=int(p['ExecMainExitTimestampMonotonic'])
    # The watchdog shares the revoke lock: never wait under that lock.
    # A bounded in-progress oneshot can defer, but cannot renew authority.
    if p['ActiveState']=='activating' and p['SubState']=='start' and 0<start<=now and now-start<=20_000_000:
        raise WatchdogBusy('STAGE39_WATCHDOG_BUSY_DEFER_NO_RENEW')
    require(p['ActiveState']=='inactive' and p['SubState']=='dead' and p['Result']=='success' and p['ExecMainCode']=='1' and p['ExecMainStatus']=='0','WATCHDOG_LAST_RUN_FAILED')
    require(0<start<=end<=now and now-end<=180_000_000,'WATCHDOG_STALE')

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
    # F08: never let an error on one path prevent attempting the other.
    results=[]
    errors=[]
    for label,path in (('GATE',GATE),('PERMIT',PERMIT)):
        try:
            results.append(quarantine_one(path))
        except (Stop,OSError,ValueError,TypeError):
            results.append(False)
            errors.append(label)
    for label,path in (('GATE',GATE),('PERMIT',PERMIT)):
        try:
            if exists(path):errors.append(label)
        except (Stop,OSError,ValueError,TypeError):
            errors.append(label)
    if errors:
        raise Stop('STAGE39_STOP_PARTIAL_REVOKE_'+','.join(sorted(set(errors))))
    return tuple(results)

def closed(doc, reason):
    doc = dict(doc)
    doc['status'] = 'fault' if reason != 'OPERATOR_CLOSED' and reason != 'HARD_DEADLINE' else 'closed'
    doc['reason'] = reason[:119]
    session_write(doc)
    return doc

def start(now=None, *, continuous=False):
    now = int(time.time()) if now is None else int(now)
    preflight_start()
    with guard_lock():
        previous = session_read()
        require(previous is None or previous['status'] in ('closed', 'fault'), 'SESSION_ALREADY_ACTIVE')
        require(not exists(GATE) and not exists(PERMIT), 'CAPABILITIES_ALREADY_PRESENT')
        current = {'version': 1, 'session_id': uuid.uuid4().hex, 'task_id': TASK, 'config_sha256': CFG_SHA,
                   'boot_id': boot_id(), 'created_unix': now, 'deadline_unix': now + MAX_SESSION_SECONDS,
                   'status': 'arming', 'reason': '', 'last_renew_unix': now}
        if continuous:
            current.update(version=2, deadline_unix=None, last_renew_mono_us=mono_us())
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
    print('STAGE39_ABSOLUTE_DEADLINE_UNIX', current['deadline_unix'] if current['deadline_unix'] is not None else 'NONE_OPERATOR_CONTINUOUS')
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
        expired = doc['deadline_unix'] is not None and now >= doc['deadline_unix']
        if expired or doc['boot_id'] != boot_id():
            revoke_locked()
            closed(doc, 'HARD_DEADLINE' if expired else 'BOOT_CHANGED')
            print('STAGE39_KEEPER_CLOSED_AT_BOUNDARY')
            return
        try:
            owner()
            for p in (HOME, HOME / '.config', CAP, CFG.parent, ROOT): trusted_dir(p)
            check_config()
            if doc['version']==2:
                delta_us=mono_us()-doc['last_renew_mono_us']
                wall_delta=now-doc['last_renew_unix']
                require(0<=delta_us<=180_000_000 and 0<=wall_delta<=180 and abs(wall_delta-delta_us/1_000_000)<=30,'CONTINUOUS_CLOCK_OR_KEEPER_STALE')
            validate_gate(now)
            perm = validate_permit(now, doc['deadline_unix'])
            watchdog_running()
            health()
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
            if doc['version']==2: doc['last_renew_mono_us']=mono_us()
            session_write(doc)
            print('STAGE39_KEEPER_RETAINED_VALID_SESSION')
        except WatchdogBusy:
            print('STAGE39_WATCHDOG_BUSY_DEFER_NO_RENEW')
        except (Stop, OSError, ValueError, TypeError, subprocess.TimeoutExpired) as exc:
            reason = str(exc) if isinstance(exc, Stop) else type(exc).__name__
            revoke_locked()
            closed(doc, 'FAIL_CLOSED_'+reason[:104])
            print('STAGE39_KEEPER_FAULT_REVOKED_BOTH')
            print('STAGE39_KEEPER_REASON', reason[:104])

def stop():
    owner()
    with guard_lock():
        # F08: revoke both active capabilities FIRST, even if session.json is corrupt.
        a,b=revoke_locked()
        try:
            doc=session_read()
        except (Stop,OSError,ValueError,TypeError):
            print('STAGE39_OPERATOR_STOP_CAPABILITIES_REVOKED_SESSION_UNREADABLE')
            raise Stop('STAGE39_STOP_SESSION_UNREADABLE_CAPABILITIES_REVOKED')
        if doc is not None and doc['status'] not in ('closed','fault'):
            closed(doc,'OPERATOR_CLOSED')
    print('STAGE39_OPERATOR_STOP_REVOKED_GATE',a)
    print('STAGE39_OPERATOR_STOP_REVOKED_PERMIT',b)
    print('STAGE39_ACTIVE_CAPABILITIES_ABSENT')

def status():
    owner()
    doc = session_read()
    print('STAGE39_SESSION_STATUS', doc['status'] if doc is not None else 'NOT_STARTED')
    if doc:
        print('STAGE39_SESSION_ID', doc['session_id'])
        print('STAGE39_SESSION_DEADLINE_UNIX', doc['deadline_unix'])
        print('STAGE39_SESSION_SECONDS_LEFT', 'CONTINUOUS' if doc['deadline_unix'] is None else max(0, doc['deadline_unix'] - int(time.time())))
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
        require(len(sys.argv) == 2 and sys.argv[1] in ('start', 'start-continuous', 'tick', 'stop', 'status'), 'USAGE')
        {'start': start, 'start-continuous': lambda: start(continuous=True), 'tick': tick, 'stop': stop, 'status': status}[sys.argv[1]]()
    except Stop as exc:
        print(str(exc), file=sys.stderr, flush=True)
        raise SystemExit(2)
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print('STAGE39_STOP_' + type(exc).__name__, file=sys.stderr, flush=True)
        raise SystemExit(2)

if __name__ == '__main__': main()
