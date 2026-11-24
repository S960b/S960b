#!/usr/bin/env python3
"""Stage35N: narrow, one-shot canonicalization of existing header secret.

No OAuth, no MCP source/unit edits, no writes to PostgreSQL, no edits to plugins.
Important: a newline is only a hypothesis for the remote 403, not proven root cause.
"""
from __future__ import annotations
import hashlib
import http.client
import json
import os
from pathlib import Path
import pwd
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time

HOME=Path('/home/kali')
ROOT=HOME/'hermes-pg-stage35n-key-canonical'
SECRET=HOME/'.config/bridge-pg-v2/managed-mcp-local-key35c'
PERMIT=HOME/'.config/bridge-pg-v2/managed-mcp-write-permit35c.json'
CFG=HOME/'.config/gigatool/opencode.json'
CFG_SHA='e5320f6e971073d5d2c43a64ce85d165cb49dc9a94e6cf7806009ebc2b59ded1'
LAUNCHER=HOME/'hermes-pg-stage35jk-cutover/run_managed_tunnel.sh'
LAUNCHER_SHA='9c41cf36448069031669fcdd502507e10b78541734ffea1e7ace4ee6c10e485a'
L_ACTIVE=HOME/'hermes-pg-stage35l-recovery-cutover/stage35l-active.json'
L_ROLL=HOME/'hermes-pg-stage35l-recovery-cutover/stage35l-rolled-back.json'
NEW='hermes-pg-tunnel-stage35jk.service'
OLD='hermes-pg-tunnel-stage33fd.service'
MCP='hermes-pg-stage35c-static-shadow.service'
ATTEMPT=ROOT/'stage35n-attempt.json'
RESULT=ROOT/'stage35n-result.json'
NEWLINE_RE=re.compile(rb'[A-Za-z0-9_-]{64}\n\Z')
PURE_RE=re.compile(rb'[A-Za-z0-9_-]{64}\Z')

class StageStop(Exception): pass

def need(condition,code):
    if not condition:raise StageStop(code)

def mark(code):
    print('STAGE35N_'+code,flush=True)

def secure_file(path,*,mode=None):
    try: s=path.lstat()
    except OSError:return False
    return (stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid() and
            s.st_nlink==1 and not (s.st_mode & 0o022) and
            (mode is None or stat.S_IMODE(s.st_mode)==mode))

def safe_secret(path):
    need(secure_file(path,mode=0o600),'SECRET_UNSAFE_OWNER_MODE_OR_LINK')
    # Catch changed inode while reading by using O_NOFOLLOW and fstat.
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    try:
        s=os.fstat(fd)
        need(stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid() and
             stat.S_IMODE(s.st_mode)==0o600 and s.st_nlink==1,
             'SECRET_OPEN_CHANGED')
        value=os.read(fd,1024)
        need(os.read(fd,1)==b'','SECRET_TOO_LONG')
        return value
    finally:os.close(fd)

def validate_secret(raw):
    if PURE_RE.fullmatch(raw):return 'canonical',raw
    if NEWLINE_RE.fullmatch(raw):return 'newline',raw[:-1]
    raise StageStop('SECRET_UNEXPECTED_FORMAT_NO_MUTATION')

def atomic_secret(path,data):
    """Write replacement inode with 0600, sync, then atomically rename.

    Path is fixed and mode/owner are checked before each replacement. Must not
    be called on symlinks, hardlinks, or attacker-writable directories.
    """
    need(secure_file(path,mode=0o600),'SECRET_CHANGED_BEFORE_REPLACE')
    parent=path.parent
    ps=parent.lstat()
    need(stat.S_ISDIR(ps.st_mode) and ps.st_uid==os.geteuid() and
         not(ps.st_mode & 0o022),'SECRET_PARENT_UNSAFE')
    need(re.fullmatch(rb'[A-Za-z0-9_-]{64}\n?',data) is not None,'WRITE_SECRET_FORMAT_DENIED')
    fd=None; tmp=None
    try:
        fd,tmp=tempfile.mkstemp(prefix='.stage35n-key-',dir=str(parent))
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'wb',closefd=True) as f:
            fd=None
            f.write(data);f.flush();os.fsync(f.fileno())
        need(secure_file(path,mode=0o600),'SECRET_CHANGED_DURING_REPLACE')
        os.replace(tmp,path)
        tmp=None
        dirfd=os.open(parent,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
        try:os.fsync(dirfd)
        finally:os.close(dirfd)
    finally:
        if fd is not None:os.close(fd)
        if tmp is not None:
            try:os.unlink(tmp)
            except FileNotFoundError:pass

def journal(path,obj):
    blob=(json.dumps(obj,sort_keys=True,separators=(',',':'))+'\n').encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'wb') as f:
        f.write(blob);f.flush();os.fsync(f.fileno())

def call(*args,timeout=18):
    return subprocess.run(args,capture_output=True,text=True,timeout=timeout)

def active(unit):
    return call('systemctl','--user','is-active','--quiet',unit,timeout=8).returncode==0

def systemctl(*args,timeout=35):
    r=call('systemctl','--user',*args,timeout=timeout)
    need(r.returncode==0,'SYSTEMCTL_'+re.sub('[^a-zA-Z0-9_]','_','_'.join(args))[:48])
    return r.stdout.strip()

def port_check(port,path,needle=None):
    c=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
    r=None
    try:
        c.request('GET',path,headers={'Connection':'close'})
        r=c.getresponse();data=r.read(512)
        return r.status==200 and (needle is None or needle in data)
    except (OSError,TimeoutError,http.client.HTTPException):return False
    finally:
        if r is not None:r.close()
        c.close()

def await_ready(seconds=70):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if active(NEW) and active(MCP) and not active(OLD) and \
            port_check(18778,'/readyz') and \
            port_check(18777,'/ping',needle=b'stage35c-static-shadow'):
            return True
        time.sleep(1)
    return False

def service_pid():
    data=systemctl('show',NEW,'--property=MainPID','--value')
    need(data.isdecimal() and int(data)>0,'MAIN_PID_NOT_RUNNING')
    return int(data)

def active_preflight():
    need(os.getuid()==os.geteuid()!=0 and pwd.getpwuid(os.getuid()).pw_name=='kali'
         and Path.home()==HOME and Path.cwd().resolve()==ROOT,'WRONG_USER_OR_DIRECTORY')
    r=ROOT.lstat()
    need(stat.S_ISDIR(r.st_mode) and r.st_uid==os.geteuid() and not(r.st_mode&0o077),
         'PACKAGE_ROOT_NOT_PRIVATE')
    need(secure_file(LAUNCHER) and hashlib.sha256(LAUNCHER.read_bytes()).hexdigest()==LAUNCHER_SHA,
         'PINNED_LAUNCHER_MISSING_OR_DRIFT')
    txt=LAUNCHER.read_text('utf8')
    need('MCP_OAUTH_DISCOVERY=\'disabled\'' in txt and
         'MCP_EXTRA_HEADERS=\'X-Bridge-Local-Key: file:'+str(SECRET)+'\'' in txt,
         'LAUNCHER_NO_EXPECTED_LOCAL_KEY')
    need(secure_file(CFG) and hashlib.sha256(CFG.read_bytes()).hexdigest()==CFG_SHA,
         'PLUGIN_CONFIG_CHANGED')
    conf=json.loads(CFG.read_text('utf8'))
    need(isinstance(conf.get('plugin'),list) and len(conf['plugin'])==7,
         'PLUGIN_COUNT_CHANGED')
    need(not PERMIT.exists() and not PERMIT.is_symlink(),'DB_WRITE_PERMIT_PRESENT')
    need(secure_file(L_ACTIVE,mode=0o600) and not L_ROLL.exists() and not L_ROLL.is_symlink(),
         '35L_NOT_ACTIVE_OR_ALREADY_ROLLED_BACK')
    state=json.loads(L_ACTIVE.read_text('utf8'))
    need(state.get('new_tunnel_active') is True and state.get('old_tunnel_inactive') is True,
         '35L_ACTIVE_MARKER_INVALID')
    unit_exec=systemctl('show',NEW,'--property=ExecStart','--value')
    need(str(LAUNCHER) in unit_exec,'NEW_TUNNEL_UNIT_EXECSTART_DRIFT')
    need(active(NEW) and not active(OLD) and active(MCP),'TUNNEL_SERVICE_STATE_DRIFT')
    need(port_check(18778,'/readyz') and
         port_check(18777,'/ping',needle=b'stage35c-static-shadow'),
         'TUNNEL_OR_MCP_NOT_READY_BEFORE_CHANGE')
    mark('PREFLIGHT_PINNED_NEW_TUNNEL_ACTIVE_7_PLUGINS_DB_READONLY')

def canonicalize_then_restart(path,raw,restart,healthy,emit):
    mode,wanted=validate_secret(raw)
    need(mode=='newline','CANONICAL_ALREADY_OR_NO_NEWLINE_NO_RESTART')
    changed=False
    try:
        atomic_secret(path,wanted)
        changed=True
        emit('KEY_CANONICALIZED_65_TO_64_BYTES_SECRET_NOT_PRINTED')
        restart()
        need(healthy(),'NEW_TUNNEL_HEALTH_AFTER_RESTART_FAILED')
    except BaseException:
        # Handle interruptions even between rename and the changed=True line.
        current=safe_secret(path)
        if current==wanted:
            emit('ROLLBACK_RESTORING_ORIGINAL_SECRET')
            atomic_secret(path,raw)
            emit('ROLLBACK_ORIGINAL_65_BYTE_SECRET_RESTORED')
            try:
                restart()
                if healthy():emit('ROLLBACK_ORIGINAL_TUNNEL_RESTART_HEALTHY')
                else:emit('CRITICAL_TUNNEL_NOT_READY_AFTER_RESTORE')
            except BaseException as e:
                emit('CRITICAL_RESTART_AFTER_ROLLBACK_'+type(e).__name__)
        elif current!=raw:
            emit('CRITICAL_SECRET_CHANGED_UNEXPECTEDLY_NO_BLIND_RESTORE')
        raise
    return wanted

def apply_once():
    active_preflight()
    need(not ATTEMPT.exists() and not ATTEMPT.is_symlink() and
         not RESULT.exists() and not RESULT.is_symlink(),'ALREADY_ATTEMPTED')
    raw=safe_secret(SECRET)
    mode,_=validate_secret(raw)
    need(mode=='newline','SECRET_NOT_NEWLINE_TERMINATED_NO_ACTION')
    original_pid=service_pid()
    journal(ATTEMPT,{'stage':'35N','attempted':True,'db_write':False,
                     'auth_root_cause_unproven':True})
    mark('ONE_SHOT_MARKED_BEFORE_SECRET_CHANGE')
    failure=None
    try:
        def postcheck():
            if not await_ready(65):return False
            need(safe_secret(SECRET)==raw[:-1],'SECRET_MISMATCH_AFTER_RESTART')
            need(service_pid()!=original_pid,'TUNNEL_PID_DID_NOT_CHANGE')
            active_preflight()
            return True
        canonicalize_then_restart(SECRET,raw,
             lambda:systemctl('restart',NEW,timeout=45),postcheck,mark)
        journal(RESULT,{'stage':'35N','success':True,'key_bytes':64,
                         'remote_chatgpt_mcp_check_pending':True,
                         'db_write':False,'auth_root_cause_unproven':True})
        mark('CANONICAL_KEY_TUNNEL_RESTART_SUCCESS_REMOTE_CONNECTOR_TEST_REQUIRED')
    except BaseException as e:
        failure=e
        if RESULT.exists():mark('CRITICAL_RESULT_MARKER_UNEXPECTEDLY_CREATED')
    if failure is not None:
        raise failure

def verify():
    active_preflight()
    need(secure_file(ATTEMPT,mode=0o600) and secure_file(RESULT,mode=0o600),
         'MISSING_RESULT_MARKERS')
    need(json.loads(RESULT.read_text('utf8')).get('success') is True,'NOT_MARKED_SUCCESS')
    mode,normalized=validate_secret(safe_secret(SECRET))
    need(mode=='canonical' and len(normalized)==64,'KEY_NOT_CANONICAL')
    mark('VERIFY_64_BYTE_KEY_NEW_TUNNEL_ACTIVE_REMOTE_PENDING')

def _signal(signum,frame):
    raise StageStop('SIGNAL_'+str(signum))

def main():
    try:
        if sys.argv[1:]==['apply-once']:
            signal.signal(signal.SIGINT,_signal);signal.signal(signal.SIGTERM,_signal)
            apply_once()
        elif sys.argv[1:]==['verify']:verify()
        else:raise StageStop('INVALID_SUBCOMMAND')
        return 0
    except BaseException as e:
        code=str(e) if isinstance(e,StageStop) else type(e).__name__
        mark('STOP_'+re.sub('[^A-Za-z0-9_]','_',code)[:100])
        return 2

if __name__=='__main__':raise SystemExit(main())
