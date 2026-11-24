#!/usr/bin/env python3
"""Stage 38: deliberately narrow, operator-initiated managed dialogue window.

Only creates existing guarded capability files, with atomic exclusive creation.
Never changes PostgreSQL, plugin config, services, SDK, dispatch or messages.
Revocation is delegated to independent installed Stage37A command.
"""
from __future__ import annotations
import hashlib
import fcntl
import json
import os
import pwd
import stat
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

HOME = Path('/home/kali')
CONFIG = HOME/'.config/gigatool/opencode.json'
CAP = HOME/'.config/bridge-pg-v2'
PERMIT = CAP/'managed-mcp-write-permit35c.json'
GATE = CAP/'managed-writes-stage34d-armed'
WATCHDOG_LOCK = CAP/'.stage37a-revoke.lock'
CANARY = HOME/'hermes-pg-stage33i-ackfix/mt-pg-inbox-agent-stage33i.mjs'
CANDIDATE = HOME/'hermes-pg-stage37b2c-claim-intent/mt-pg-inbox-managed-stage37b2c-unregistered.mjs'
TASK = 'task_b6c26aa18220412bafdbf32393f16568'
CFG_SHA = 'f46c3d4c2a46bd28d545ecd8a4c435e9cb6f0938723cdd106cbb58e6aa4d0bf5'
CANDIDATE_PREFIX = '06be5ea765f0'
STATE = HOME/'.local/state/mt-pg-inbox-managed-stage34e/state.json'
INTENTS = HOME/'.local/state/mt-pg-inbox-managed-stage34e/claim-intents37b2c'
GATE_BYTES = b'ALLOW_MANAGED_WRITES_AFTER_EXCLUSIVE_SDK_REVIEW\n'
WATCHDOG = 'hermes-pg-stage37a-permit-watchdog.timer'
REVOKE = HOME/'hermes-pg-stage37a-permission-control/371_emergency_revoke.sh'
PERMIT_SEC = 3600
GATE_SEC_MAX = 1200

class Stop(Exception): pass

def require(x, reason):
    if not x: raise Stop('STAGE38_STOP_'+reason)

def hexdigest(raw):return hashlib.sha256(raw).hexdigest()

def trusted_dir(p):
    s=p.lstat()
    require(stat.S_ISDIR(s.st_mode) and not p.is_symlink() and s.st_uid==os.geteuid() and (s.st_mode&0o022)==0,'UNTRUSTED_DIR_'+p.name)

def file_bytes(p, limit=150000):
    s=p.lstat()
    require(stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid() and s.st_nlink==1 and not p.is_symlink() and (s.st_mode&0o022)==0 and 0<s.st_size<=limit,'UNTRUSTED_FILE_'+p.name)
    f=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        t=os.fstat(f)
        require((s.st_dev,s.st_ino,s.st_size)==(t.st_dev,t.st_ino,t.st_size),'FILE_RACE_'+p.name)
        raw=os.read(f,limit+1)
        require(len(raw)==s.st_size,'CHANGED_WHILE_READING_'+p.name)
        return raw
    finally:os.close(f)

def no_file(p):
    require(not p.exists() and not p.is_symlink(),'ACTIVE_CAPABILITY_ALREADY_PRESENT_'+p.name)

def dirsync(p):
    f=os.open(p,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(f)
    finally:os.close(f)

def exclusive_private(p, raw):
    f=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        os.write(f,raw);os.fsync(f)
    finally:os.close(f)
    dirsync(p.parent)

def check_scope(cfg):
    require(isinstance(cfg,dict) and isinstance(cfg.get('plugin'),list) and len(cfg['plugin'])==8,'CONFIG_NOT_EIGHT_PLUGINS')
    entries=cfg['plugin']
    names=[e if isinstance(e,str) else e[0] if isinstance(e,list) and len(e)==2 and isinstance(e[0],str) and isinstance(e[1],dict) else None for e in entries]
    require(len(set(names))==8 and names.count(str(CANDIDATE))==1 and names.index(str(CANDIDATE))==7 and names.count(str(CANARY))==1,'PLUGIN_PATHS_CHANGED')
    require(entries[7][1]=={'dry_run':False,'tick_ms':30000,'task_ids':[TASK]},'PLUGIN_SCOPE_CHANGED')

def preflight():
    require(os.getuid()==os.geteuid()!=0 and pwd.getpwuid(os.getuid()).pw_name=='kali' and Path.home()==HOME and os.environ.get('HOME')==str(HOME),'WRONG_OWNER')
    for p in (HOME,HOME/'.config',CAP,CONFIG.parent):trusted_dir(p)
    config_bytes=file_bytes(CONFIG,2097152)
    require(hexdigest(config_bytes)==CFG_SHA,'CONFIG_SHA_CHANGED')
    check_scope(json.loads(config_bytes))
    require(hexdigest(file_bytes(CANDIDATE,250000)).startswith(CANDIDATE_PREFIX),'CANDIDATE_SHA_CHANGED')
    require(REVOKE.is_file() and not REVOKE.is_symlink(),'EMERGENCY_REVOKE_MISSING')
    r=subprocess.run(['systemctl','--user','is-active','--quiet',WATCHDOG],timeout=8,capture_output=True)
    require(r.returncode==0,'WATCHDOG_NOT_RUNNING')
    for url in ('http://127.0.0.1:18777/ping','http://127.0.0.1:18778/readyz'):
        try:
            with urllib.request.urlopen(url,timeout=4) as req:good=req.status==200
        except Exception:good=False
        require(good,'MCP_OR_TUNNEL_UNHEALTHY')
    state=json.loads(file_bytes(STATE,65536))
    require(isinstance(state,dict) and state.get('inflight')=={} and isinstance(state.get('seen'),dict) and state['seen'].get(TASK,0)>=1,'INFLIGHT_OR_CURSOR_UNSAFE')
    if INTENTS.exists() or INTENTS.is_symlink():
        trusted_dir(INTENTS)
        require(not list(INTENTS.iterdir()),'UNRESOLVED_CLAIM_INTENT')
    return config_bytes

@contextmanager
def locked_against_watchdog():
    fd=os.open(WATCHDOG_LOCK,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        st=os.fstat(fd)
        require(stat.S_ISREG(st.st_mode) and st.st_nlink==1 and st.st_uid==os.geteuid() and (st.st_mode&0o077)==0,'WATCHDOG_LOCK_UNTRUSTED')
        fcntl.flock(fd,fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd,fcntl.LOCK_UN)
        os.close(fd)

def arm():
    preflight()
    now=int(time.time())
    permit={
        'version':1,'expires_unix':now+PERMIT_SEC,'config_sha256':CFG_SHA,
        'task_ids':[TASK],'allow_create':False,'static_tunnel_verified':True
    }
    blob=(json.dumps(permit,sort_keys=True,separators=(',',':'))+'\n').encode()
    # Same advisory lock as Stage37A watchdog: prevent it from revoking the
    # gate between the two exclusive creates. Gate-first makes a crash before
    # permit creation fail closed on the ChatGPT side.
    with locked_against_watchdog():
        no_file(PERMIT);no_file(GATE)
        exclusive_private(GATE,GATE_BYTES)
        try:
            exclusive_private(PERMIT,blob)
            require(PERMIT.exists() and GATE.exists(),'ARM_FILES_NOT_PRESENT')
        except BaseException:
            # Do not call independent revoker under the same lock (deadlock).
            # Safely quarantine our capabilities under the pinned directory.
            for path in (GATE,PERMIT):
                if path.exists() or path.is_symlink():
                    dest=CAP/('.stage38-aborted-'+path.name+'-'+str(os.getpid())+'-'+str(time.time_ns()))
                    os.rename(path,dest)
            dirsync(CAP)
            print('STAGE38_ARM_ABORTED_CAPABILITIES_QUARANTINED',flush=True)
            raise
    print('STAGE38_WINDOW_OPEN_ONE_EXISTING_TASK_ONLY')
    print('STAGE38_TASK_ID',TASK)
    print('STAGE38_CONFIG_SHA_PREFIX',CFG_SHA[:12])
    print('STAGE38_ALLOW_CREATE_FALSE')
    print('STAGE38_MCP_PERMIT_EXPIRY_UNIX',now+PERMIT_SEC)
    print('STAGE38_AGENT_GATE_MAX_SECONDS',GATE_SEC_MAX)
    print('STAGE38_WATCHDOG_ACTIVE')
    print('STAGE38_NEXT_CHATGPT_ENABLE_DISPATCH_AND_SEND_ONE_TASK')
    print('STAGE38_NO_DB_SDK_CONFIG_OR_SERVICE_MUTATION')

def main():
    try:
        require(len(sys.argv)==2 and sys.argv[1]=='arm','USAGE')
        arm()
    except Stop as e:
        print(str(e),file=sys.stderr,flush=True)
        raise SystemExit(2)
    except (OSError,ValueError,TypeError,subprocess.TimeoutExpired) as e:
        print('STAGE38_STOP_'+type(e).__name__,file=sys.stderr,flush=True)
        raise SystemExit(2)

if __name__=='__main__':main()
