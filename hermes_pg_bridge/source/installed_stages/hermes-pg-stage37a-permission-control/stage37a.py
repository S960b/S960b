#!/usr/bin/env python3
"""Stage37A: independent, fail-closed emergency revocation of local write capabilities.

No database, tunnel, plugin, SDK, or service changes. Never arms a permit or gate.
Both capabilities use fixed *active* paths; config SHA drift must not block revocation.
Quarantine is performed by same-directory rename without following symlinks.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import pwd
import re
import stat
import sys
import time
from contextlib import contextmanager
from pathlib import Path

HOME = Path('/home/kali')
ACTIVE_DIR = Path('.config/bridge-pg-v2')
CFG = Path('.config/gigatool/opencode.json')
PERMIT_NAME = 'managed-mcp-write-permit35c.json'
GATE_NAME = 'managed-writes-stage34d-armed'
LOCK_NAME = '.stage37a-revoke.lock'
GATE_BYTES = b'ALLOW_MANAGED_WRITES_AFTER_EXCLUSIVE_SDK_REVIEW\n'
MAX_CLI_GATE_SECONDS = 1200
TASK_RE = re.compile(r'^task_[0-9a-f]{32}$')
SHA_RE = re.compile(r'^[0-9a-f]{64}$')
CANARY = 'task_f2c64cfdf7894537b35a19a6def977c6'
PERMIT_KEYS = {'version','expires_unix','config_sha256','task_ids','allow_create','static_tunnel_verified'}

class Stop(RuntimeError):
    pass

def need(yes: bool, reason: str) -> None:
    if not yes:
        raise Stop('STAGE37A_STOP_' + reason)

def user_check():
    need(os.getuid() == os.geteuid() != 0, 'RUN_AS_UNPRIVILEGED_OWNER')
    need(pwd.getpwuid(os.getuid()).pw_name == 'kali', 'USER_NOT_KALI')
    need(Path(os.environ.get('HOME','')) == HOME and Path.home() == HOME, 'HOME_MISMATCH')

def verify_directory(path: Path):
    st=path.lstat()
    need(stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid() and not (st.st_mode & 0o022),
         'UNTRUSTED_DIRECTORY')

def open_active_dir(home: Path) -> int:
    # Open and pin the directory; never use the caller's CWD or follow a symlink.
    p=home/ACTIVE_DIR
    for directory in (home,home / ".config",p):
        verify_directory(directory)
    fd=os.open(p, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    st=os.fstat(fd)
    need(stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid() and not (st.st_mode & 0o022),
         'UNTRUSTED_DIRECTORY_HANDLE')
    return fd

def stat_entry(fd: int, name: str):
    try:
        return os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return None

def load_private_permit(home: Path, fd: int, now: float) -> tuple[bool,str]:
    st=stat_entry(fd,PERMIT_NAME)
    if st is None:
        return False,'ABSENT'
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_nlink != 1 or (st.st_mode & 0o077):
        return False,'UNSAFE_FILE'
    if st.st_size > 4096 or st.st_size < 2:
        return False,'SIZE_INVALID'
    f=None
    try:
        f=os.open(PERMIT_NAME,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
        fst=os.fstat(f)
        if (fst.st_ino,fst.st_dev)!=(st.st_ino,st.st_dev):
            return False,'REPLACED_DURING_READ'
        with os.fdopen(f,'rb') as stream:
            f=None
            raw=stream.read(4097)
        data=json.loads(raw.decode('utf8'))
    except (OSError,ValueError,UnicodeError,TypeError):
        return False,'UNREADABLE_OR_INVALID_JSON'
    finally:
        if f is not None:
            os.close(f)
    if not isinstance(data,dict) or set(data)!=PERMIT_KEYS:
        return False,'INVALID_SCHEMA'
    if type(data['version']) is not int or data['version']!=1:
        return False,'INVALID_VERSION'
    if type(data['static_tunnel_verified']) is not bool or data['static_tunnel_verified'] is not True:
        return False,'INVALID_TUNNEL_PIN'
    if type(data['expires_unix']) is not int or not now < data['expires_unix'] <= now+86400:
        return False,'EXPIRED_OR_BAD_EXPIRY'
    if type(data['allow_create']) is not bool:
        return False,'INVALID_CREATE_FIELD'
    ids=data['task_ids']
    if (not isinstance(ids,list) or len(ids)>1 or
        any(not isinstance(x,str) or not TASK_RE.fullmatch(x) or x==CANARY for x in ids)):
        return False,'INVALID_SCOPE'
    if not isinstance(data['config_sha256'],str) or not SHA_RE.fullmatch(data['config_sha256']):
        return False,'INVALID_CONFIG_PIN'
    c=home/CFG
    try:
        cst=c.lstat()
        if (not stat.S_ISREG(cst.st_mode) or cst.st_uid!=os.geteuid() or
            cst.st_size>2097152 or cst.st_nlink!=1):
            return False,'CONFIG_UNTRUSTED'
        current=hashlib.sha256(c.read_bytes()).hexdigest()
    except OSError:
        return False,'CONFIG_UNREADABLE'
    if current!=data['config_sha256']:
        return False,'CONFIG_DRIFT'
    return True, ('VALID_SCOPED' if not data['allow_create'] and len(ids)==1 else
                  'VALID_CREATE' if data['allow_create'] and len(ids)==0 else
                  'VALID_OTHER')

def validate_gate(fd: int, now: float) -> str:
    st=stat_entry(fd,GATE_NAME)
    if st is None:
        return 'ABSENT'
    if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_nlink!=1 or st.st_mode & 0o077:
        return 'UNSAFE_GATE'
    if st.st_size != len(GATE_BYTES):
        return 'UNKNOWN_GATE'
    if st.st_mtime > now + 60 or now - st.st_mtime >= MAX_CLI_GATE_SECONDS:
        return 'EXPIRED_GATE'
    f=None
    try:
        f=os.open(GATE_NAME,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
        fst=os.fstat(f)
        if (fst.st_dev,fst.st_ino)!=(st.st_dev,st.st_ino):
            return 'REPLACED_GATE'
        with os.fdopen(f,'rb') as stream:
            f=None
            return 'VALID' if stream.read(128)==GATE_BYTES else 'UNKNOWN_GATE'
    except OSError:
        return 'UNREADABLE_GATE'
    finally:
        if f is not None:os.close(f)

@contextmanager
def locked(fd: int):
    lock=os.open(LOCK_NAME, os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=fd)
    try:
        fst=os.fstat(lock)
        need(stat.S_ISREG(fst.st_mode) and fst.st_uid == os.geteuid() and
             fst.st_nlink==1 and not fst.st_mode & 0o077,'UNTRUSTED_LOCK')
        fcntl.flock(lock,fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(lock,fcntl.LOCK_UN)
        os.close(lock)

def quarantine(fd: int, name: str) -> bool:
    """Remove *only* the active directory entry; never delete backup data or follow links."""
    for attempt in range(4):
        st=stat_entry(fd,name)
        if st is None:
            return attempt>0
        need(not stat.S_ISDIR(st.st_mode), 'ACTIVE_PATH_IS_DIRECTORY')
        dest=f'.revoked-stage37a-{name}-{time.time_ns()}-{os.getpid()}-{attempt}.bak'
        # Both names relative to the same trusted open directory descriptor.
        need(stat_entry(fd,dest) is None,'QUARANTINE_COLLISION')
        os.rename(name,dest,src_dir_fd=fd,dst_dir_fd=fd)
        os.fsync(fd)
        # Ensure active entry is gone, even after a concurrent recreation.
        if stat_entry(fd,name) is None:
            return True
    need(False,'ACTIVE_PATH_REAPPEARED')
    return False

def status(home: Path, now: float | None = None) -> dict:
    now=time.time() if now is None else now
    fd=open_active_dir(home)
    try:
        valid,code=load_private_permit(home,fd,now)
        gs=validate_gate(fd,now)
        return {'gate_state':gs,'permit_present':stat_entry(fd,PERMIT_NAME) is not None,
                'permit_valid':valid,'permit_state':code,
                'cli_gate_present':stat_entry(fd,GATE_NAME) is not None}
    finally:
        os.close(fd)

def _revoke_both_independently(fd: int) -> dict:
    """F08: never skip the second path when the first rename/fsync fails."""
    outcomes={'gate_quarantined':False,'permit_quarantined':False}
    failures=[]
    for name,field in ((GATE_NAME,'gate_quarantined'),(PERMIT_NAME,'permit_quarantined')):
        try:
            outcomes[field]=quarantine(fd,name)
        except (Stop,OSError,ValueError,TypeError):
            failures.append(name)
    for name in (GATE_NAME,PERMIT_NAME):
        try:
            if stat_entry(fd,name) is not None:failures.append(name)
        except (Stop,OSError,ValueError,TypeError):
            failures.append(name)
    if failures:
        # The other path has still been attempted. Caller must not announce full closure.
        raise Stop('STAGE37A_STOP_PARTIAL_REVOKE_'+','.join(sorted(set(failures))))
    return outcomes

def revoke(home: Path) -> dict:
    fd=open_active_dir(home)
    try:
        with locked(fd):
            return _revoke_both_independently(fd)
    finally:
        os.close(fd)

def watchdog(home: Path,now: float | None = None) -> dict:
    now=time.time() if now is None else now
    fd=open_active_dir(home)
    try:
        with locked(fd):
            valid,code=load_private_permit(home,fd,now)
            gs=validate_gate(fd,now)
            gate=gs!='ABSENT'
            if not valid:
                # Order matters: CLI gate first, then invalid/expired MCP permit.
                result=_revoke_both_independently(fd)
                return {'action':'REVOKED_INVALID','reason':code,**result}
            if not gate:
                # Permit without the agent gate lets the gateway enqueue while the agent
                # cannot execute. Both capabilities must live and die together.
                result=_revoke_both_independently(fd)
                return {'action':'REVOKED_MISSING_GATE','reason':'gate_absent',**result}
            if code not in ('VALID_SCOPED', 'VALID_CREATE') or gs != 'VALID':
                reason=code if code not in ('VALID_SCOPED', 'VALID_CREATE') else gs
                result=_revoke_both_independently(fd)
                return {'action':'REVOKED_UNSAFE_GATE','reason':reason,**result}
            return {'action':'RETAINED_VALID','reason':code,'gate_quarantined':False,'permit_quarantined':False}
    finally:
        os.close(fd)

def main():
    try:
        user_check()
        need(len(sys.argv)==2 and sys.argv[1] in ('status','revoke','watchdog'), 'USAGE')
        cmd=sys.argv[1]
        if cmd=='status':
            s=status(HOME)
            print('STAGE37A_PERMIT_PRESENT',s['permit_present'])
            print('STAGE37A_PERMIT_RUNTIME_VALID',s['permit_valid'])
            print('STAGE37A_PERMIT_STATE',s['permit_state'])
            print('STAGE37A_CLI_GATE_PRESENT',s['cli_gate_present'])
            print('STAGE37A_CLI_GATE_STATE',s['gate_state'])
            print('STAGE37A_READONLY_NO_DB_OR_CONFIG_MUTATION')
        elif cmd=='revoke':
            r=revoke(HOME)
            print('STAGE37A_CLI_GATE_QUARANTINED' if r['gate_quarantined'] else 'STAGE37A_CLI_GATE_ALREADY_ABSENT')
            print('STAGE37A_MCP_PERMIT_QUARANTINED' if r['permit_quarantined'] else 'STAGE37A_MCP_PERMIT_ALREADY_ABSENT')
            print('STAGE37A_ACTIVE_WRITE_PATHS_ABSENT_CONFIRMED')
        else:
            r=watchdog(HOME)
            print('STAGE37A_WATCHDOG_'+r['action'])
            print('STAGE37A_WATCHDOG_REASON_'+r['reason'])
            print('STAGE37A_WATCHDOG_NO_DB_OR_CONFIG_MUTATION')
    except (Stop,OSError,ValueError,TypeError) as e:
        print(str(e) if isinstance(e,Stop) else 'STAGE37A_STOP_LOCAL_FILESYSTEM_ERROR',file=sys.stderr)
        raise SystemExit(2)

if __name__=='__main__':
    main()
