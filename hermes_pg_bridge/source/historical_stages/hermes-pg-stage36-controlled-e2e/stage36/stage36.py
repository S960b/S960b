#!/usr/bin/env python3
"""Stage 36: operator-gated, scoped, expiring MCP write-permit for one managed canary.

Only permits the already-installed runtime35c to perform staged managed operations.
Does not make SQL writes, change plugins, stop services, or invoke a tool remotely.
"""
from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HOME = Path('/home/kali')
CFG = HOME / '.config/gigatool/opencode.json'
PERMIT = HOME / '.config/bridge-pg-v2/managed-mcp-write-permit35c.json'
STATE = HOME / '.local/state/hermes-pg-stage36'
MARK_CREATE = STATE / 'arm-create.json'
MARK_SCOPE = STATE / 'scope.json'
VENV_PY = HOME / 'bridge-pg-v2/.venv/bin/python'
ACTOR = HOME / 'hermes-pg-stage33e-shadow-clients/actor_import33e.py'
RUNTIME = HOME / 'hermes-pg-stage35c-static-guard/runtime35c.py'
EXPECTED_RUNTIME_SHA = '07f5a6a909a954913843491a3ed750361bc4f66826144719f1aac6328d093641'
CANARY = 'task_f2c64cfdf7894537b35a19a6def977c6'
OLD_PILOT = 'task_bddaadef89b7458fb7f8cf437dc0f023'
TITLE = 'PGV2 Stage36 Independent Managed E2E Canary'
REQUEST_ID = 'pgv2-stage36-independent-e2e-20261123-v1'
EXPECTED_CFG_SHA = 'e5320f6e971073d5d2c43a64ce85d165cb49dc9a94e6cf7806009ebc2b59ded1'
TASK_RE = re.compile(r'task_[0-9a-f]{32}\Z')

class Stop(RuntimeError):
    pass

def require(ok: bool, code: str):
    if not ok:
        raise Stop('STAGE36_STOP_' + code)

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def is_private_file(path: Path, *, max_mode=0o600):
    if path.is_symlink():
        return False
    try:
        s=path.lstat()
    except FileNotFoundError:
        return False
    return (stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid()
            and s.st_nlink==1 and (s.st_mode & 0o077)==0
            and (s.st_mode & 0o200)!=0)

def owner_safe_dir(path: Path):
    s=path.lstat()
    return not path.is_symlink() and stat.S_ISDIR(s.st_mode) and s.st_uid==os.geteuid() and not (s.st_mode & 0o022)

def check_operator():
    require(pwd.getpwuid(os.geteuid()).pw_name=='kali','WRONG_USER')
    require(Path(os.environ.get('HOME',''))==HOME,'WRONG_HOME')
    require(PERMIT.parent.exists() and owner_safe_dir(PERMIT.parent),'PERMIT_DIRECTORY_UNSAFE')
    require(CFG.is_file() and not CFG.is_symlink() and CFG.stat().st_uid==os.geteuid(), 'CONFIG_UNSAFE')
    require(sha256(CFG.read_bytes())==EXPECTED_CFG_SHA,'SEVEN_PLUGIN_CONFIG_PIN_MISMATCH')
    require(ACTOR.is_file() and not ACTOR.is_symlink(), 'DB_ACTOR_MISSING')
    require(RUNTIME.is_file() and not RUNTIME.is_symlink() and RUNTIME.stat().st_uid==os.geteuid(), 'RUNTIME_UNSAFE')
    require(sha256(RUNTIME.read_bytes())==EXPECTED_RUNTIME_SHA,'RUNTIME_PERMIT_LOGIC_DRIFT')
    require(VENV_PY.is_file(), 'VENV_MISSING')
    if STATE.exists():
        require(owner_safe_dir(STATE) and (STATE.stat().st_mode & 0o077)==0,'STATE_DIR_UNSAFE')

def service_active(service: str, expected: bool):
    p=subprocess.run(['systemctl','--user','is-active',service],capture_output=True,text=True,timeout=8)
    require((p.returncode==0)==expected,'SERVICE_STATE_'+service.split('.')[0].replace('-','_').upper())

def ping(url: str):
    try:
        with urllib.request.urlopen(url,timeout=5) as r:
            return r.status
    except Exception:
        return -1

def preflight(*, forbid_existing_permit=False):
    check_operator()
    service_active('hermes-pg-tunnel-stage35jk.service',True)
    service_active('hermes-pg-tunnel-stage33fd.service',False)
    service_active('hermes-pg-stage35c-static-shadow.service',True)
    require(ping('http://127.0.0.1:18778/readyz')==200,'TUNNEL_NOT_READY')
    require(ping('http://127.0.0.1:18777/ping')==200,'MCP_NOT_READY')
    if forbid_existing_permit:
        require(not PERMIT.exists() and not PERMIT.is_symlink(),'WRITE_PERMIT_ALREADY_PRESENT')
    print('STAGE36_READONLY_PREFLIGHT_PINNED_TUNNEL_MCP_SEVEN_PLUGINS_OK',flush=True)

def safe_state():
    if not STATE.exists():
        STATE.mkdir(mode=0o700,parents=True,exist_ok=False)
    require(owner_safe_dir(STATE) and not (STATE.stat().st_mode & 0o077),'STATE_DIR_UNSAFE')

def create_exclusive(path: Path, data: bytes):
    fd=os.open(str(path),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb') as f:
            os.fchmod(f.fileno(),0o600)
            f.write(data);f.flush();os.fsync(f.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise

def encode(obj):
    return (json.dumps(obj,sort_keys=True,separators=(',',':'))+'\n').encode('utf8')

def permit_data(*, task_ids:list[str], allow_create:bool, expiry:int):
    return {'version':1,'expires_unix':expiry,'config_sha256':EXPECTED_CFG_SHA,
            'task_ids':task_ids,'allow_create':allow_create,'static_tunnel_verified':True}

def read_permit():
    require(is_private_file(PERMIT),'WRITE_PERMIT_FILE_UNSAFE')
    try:
        p=json.loads(PERMIT.read_text('utf8'))
    except Exception:
        raise Stop('STAGE36_STOP_PERMIT_PARSE_ERROR')
    require(isinstance(p,dict) and set(p)==set(permit_data(task_ids=[],allow_create=True,expiry=0)),'PERMIT_SCHEMA_DRIFT')
    require(p.get('config_sha256')==EXPECTED_CFG_SHA and p.get('version')==1 and p.get('static_tunnel_verified') is True,'PERMIT_CONFIG_DRIFT')
    return p

def read_managed_detail(tid):
    # Uses the pre-existing PostgreSQL role-bound read adapter only.
    sys.path.insert(0,str(ACTOR.parent))
    from actor_import33e import actor_classes
    ChatGPT,_=actor_classes()
    cg=ChatGPT()
    result=cg._one('SELECT bridge.cg_managed_detail(%s) AS result',(tid,))
    listing=cg._one('SELECT bridge.cg_list_managed_dialogs(%s,%s) AS result',(0,50))
    return result,listing

def arm_create():
    preflight(forbid_existing_permit=True)
    require(not MARK_CREATE.exists() and not MARK_CREATE.is_symlink(),'CREATE_ALREADY_ARMED')
    require(not MARK_SCOPE.exists() and not MARK_SCOPE.is_symlink(),'SCOPE_ALREADY_USED')
    _, listing=read_managed_detail(OLD_PILOT)
    require(isinstance(listing,list) and len(listing)==1 and listing[0].get('task_id')==OLD_PILOT and listing[0].get('dispatch_enabled') is False,'MANAGED_REGISTRY_NOT_BASELINE')
    safe_state()
    create_exclusive(MARK_CREATE,encode({'stage':36,'phase':'create_armed','request_id':REQUEST_ID,'created_unix':int(time.time())}))
    try:
        create_exclusive(PERMIT,encode(permit_data(task_ids=[],allow_create=True,expiry=int(time.time())+1800)))
    except BaseException:
        print('STAGE36_STOP_CREATE_MARKER_WRITTEN_PERMIT_NOT_INSTALLED_DO_NOT_RETRY',flush=True)
        raise
    print('STAGE36_CREATE_PERMIT_ARMED_30_MINUTES_NO_TASK_DISPATCH',flush=True)
    print('STAGE36_NEXT_CHATGPT_MANAGED_CREATE_REQUEST_ID_'+REQUEST_ID,flush=True)

def atomic_update_file(path:Path,data:bytes):
    require(is_private_file(path),'PERMIT_FILE_CHANGED')
    temp=path.parent/('.stage36-permit-tmp-'+str(os.getpid()))
    create_exclusive(temp,data)
    try:
        require(is_private_file(path),'PERMIT_FILE_CHANGED')
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)

def scope(tid):
    require(TASK_RE.fullmatch(tid) is not None and tid not in (CANARY,OLD_PILOT),'UNEXPECTED_TASK_ID')
    preflight()
    require(is_private_file(MARK_CREATE),'CREATE_MARKER_MISSING')
    require(not MARK_SCOPE.exists() and not MARK_SCOPE.is_symlink(),'SCOPE_ALREADY_USED')
    p=read_permit()
    require(p['allow_create'] is True and p['task_ids']==[] and int(p['expires_unix'])>time.time(),'CREATE_PERMIT_EXPIRED_OR_CHANGED')
    detail, listing=read_managed_detail(tid)
    require(isinstance(detail,dict) and detail.get('registered') is True and detail.get('task_id')==tid,'NEW_MANAGED_TASK_NOT_REGISTERED')
    require(detail.get('title')==TITLE and detail.get('state')=='active' and detail.get('dispatch_enabled') is False and detail.get('last_seq')==0 and detail.get('version')==0 and detail.get('policy_version')==0 and detail.get('next_actor')=='chatgpt','NEW_MANAGED_TASK_DRIFT')
    require(isinstance(listing,list) and len(listing)==2 and {x.get('task_id') for x in listing}=={OLD_PILOT,tid},'UNEXPECTED_REGISTRY_CHANGE')
    require(all(x.get('dispatch_enabled') is False for x in listing),'DISPATCH_ALREADY_ENABLED')
    safe_state()
    create_exclusive(MARK_SCOPE,encode({'stage':36,'phase':'scoped','task_id':tid,'scoped_unix':int(time.time())}))
    atomic_update_file(PERMIT,encode(permit_data(task_ids=[tid],allow_create=False,expiry=int(time.time())+3600)))
    print('STAGE36_PERMIT_SCOPED_ONE_TASK_ALLOW_CREATE_FALSE_60_MINUTES',flush=True)
    print('STAGE36_NEXT_CHATGPT_CAN_ENABLE_DISPATCH_FOR_CONFIRMED_TASK',flush=True)

def revoke():
    # Revocation must remain available if the plugin configuration or DB is down.
    require(pwd.getpwuid(os.geteuid()).pw_name=='kali' and Path(os.environ.get('HOME',''))==HOME,'WRONG_USER')
    require(PERMIT.parent.exists() and owner_safe_dir(PERMIT.parent),'PERMIT_DIRECTORY_UNSAFE')
    if not PERMIT.exists() and not PERMIT.is_symlink():
        print('STAGE36_PERMIT_ALREADY_ABSENT',flush=True)
        return
    p=read_permit()
    require(p['allow_create'] in (True,False) and isinstance(p.get('task_ids'),list),'PERMIT_UNKNOWN')
    PERMIT.unlink()
    require(not PERMIT.exists(),'PERMIT_REVOKE_FAILED')
    print('STAGE36_LOCAL_WRITE_PERMIT_REVOKED',flush=True)

def status():
    preflight()
    print('STAGE36_PERMIT_PRESENT',PERMIT.exists(),flush=True)
    print('STAGE36_CREATE_MARKER',MARK_CREATE.exists(),'SCOPE_MARKER',MARK_SCOPE.exists(),flush=True)
    if PERMIT.exists():
        p=read_permit()
        print('STAGE36_PERMIT_ALLOW_CREATE',p['allow_create'],'TASK_COUNT',len(p['task_ids']),
              'EXPIRES_IN_SEC',max(0,int(p['expires_unix']-time.time())),flush=True)

def main():
    try:
        require(len(sys.argv)>=2,'USAGE')
        cmd=sys.argv[1]
        if cmd=='preflight' and len(sys.argv)==2:preflight(forbid_existing_permit=True)
        elif cmd=='status' and len(sys.argv)==2:status()
        elif cmd=='arm-create' and len(sys.argv)==2:arm_create()
        elif cmd=='scope' and len(sys.argv)==3:scope(sys.argv[2])
        elif cmd=='revoke' and len(sys.argv)==2:revoke()
        else:raise Stop('STAGE36_STOP_BAD_COMMAND')
    except (Stop,FileExistsError,PermissionError,OSError,subprocess.TimeoutExpired) as e:
        print(str(e) if isinstance(e,Stop) else 'STAGE36_STOP_LOCAL_IO_OR_PROCESS_ERROR',file=sys.stderr,flush=True)
        raise SystemExit(2)

if __name__=='__main__':main()
