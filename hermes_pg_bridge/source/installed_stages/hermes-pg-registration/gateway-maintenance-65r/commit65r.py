"""Promote the verified live trial without restarting MCP; never edit the existing unit files."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import sys
import time
import types

HOME = Path('/home/kali')
BASE = HOME / 'hermes-pg-registration'
STATE = BASE / 'gateway-maintenance-65r'
TRIAL = BASE / 'trial65r-357/trial65r.py'
TRIAL_HASH = '3acfb61a130dc4296a728e6db9da323f1df967263238086e519a901ffb4424d8'
RESCUE='hermes-pg-65r-359-commit-recovery'
CG = 'task_7aa14730f31146669116c7a5875150cc'
RID = 'stage65r-chatgpt-e2e-20261128-v1'
def need(ok, code):
    if not ok:
        raise RuntimeError(code)
def secure_read(path,limit=65536):
    fd=os.open(path,os.O_NOFOLLOW|os.O_NONBLOCK|os.O_RDONLY)
    with os.fdopen(fd,'rb') as f:
        s=os.fstat(fd)
        need(stat.S_ISREG(s.st_mode) and s.st_uid==os.getuid() and s.st_nlink==1
             and not s.st_mode&0o077 and s.st_size<=limit,'untrusted_private_file')
        data=f.read(limit+1)
        need(len(data)==s.st_size,'file_changed')
        return data
def load_trial():
    data=secure_read(TRIAL)
    need(hashlib.sha256(data).hexdigest()==TRIAL_HASH,'trial_source_drift')
    m=types.ModuleType('verified_trial')
    exec(compile(data,str(TRIAL),'exec'),m.__dict__)
    return m
def trust_chain(paths):
    private=False
    for path in paths:
        s=path.lstat()
        need(stat.S_ISDIR(s.st_mode) and s.st_uid==os.getuid()
             and not s.st_mode&0o002,'unsafe_unit_directory '+str(path)+' '+oct(stat.S_IMODE(s.st_mode)))
        if s.st_mode&0o020:
            need(private and s.st_gid==os.getegid(),'shared_unit_directory '+str(path)+' '+oct(stat.S_IMODE(s.st_mode)))
        private |= stat.S_IMODE(s.st_mode)==0o700
def syncdir(p):
    fd=os.open(p,os.O_DIRECTORY|os.O_NOFOLLOW|os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)
def keep(t,path,data):
    if not os.path.lexists(path):
        t.publish(path,data)
        syncdir(path.parent)
    need(secure_read(path)==data,'existing_release_file_differs')
def remove_own(path,data):
    if os.path.lexists(path):
        need(secure_read(path)==data,'foreign_override')
        path.unlink()
        syncdir(path.parent)
def original_files(t,extra):
    index=json.loads(secure_read(t.BK/'systemd.json'))
    records=index['files']
    paths=[t.prop(t.UNIT,'FragmentPath'),*shlex.split(t.prop(t.UNIT,'DropInPaths'))]
    need(set(paths)=={r['path'] for r in records}|{str(p) for p in extra},'unit_sources_changed')
    for r in records:
        src=Path(r['path']).resolve(strict=True)
        need(hashlib.sha256(t.read(src,1048576,{0,os.getuid()})).hexdigest()==r['sha256']
             and hashlib.sha256(secure_read(t.BK/r['copy'],1048576)).hexdigest()==r['sha256'],'baseline_changed')
def timer_safe(t):
    unit=t.RESTORE+'.timer'
    need(t.prop(unit,'ActiveState')=='active' and t.prop(unit,'SubState')=='waiting','trial_timer_not_waiting')
    entered=int(t.prop(unit,'ActiveEnterTimestampMonotonic'))
    elapsed=time.clock_gettime(time.CLOCK_MONOTONIC)-entered/1000000
    need(entered>0 and 0<=elapsed<17*60,'insufficient_trial_time')
    need(t.prop(t.RESTORE+'.service','ActiveState')=='inactive','rollback_in_progress')
def evidence(t):
    t.smoke(True)
    receipt=t.tool('managed_registration_status',request_id=RID)
    need(receipt.get('found') is True and receipt.get('task_id')==CG and receipt.get('initiator')=='chatgpt'
         and receipt.get('dispatch_enabled') is False,'chatgpt_receipt_changed')
    head=t.tool('managed_status',task_id=CG)
    need(head.get('registered') is True and head.get('last_seq')==0 and head.get('dispatch_enabled') is False,
         'unexpected_canary_state')
def recover(t,perm,config):
    if (STATE/'committed.json').is_file(): return
    if os.path.lexists(perm):
        need(secure_read(perm)==config,'foreign_override')
    if t.prop(t.RESTORE+'.timer','ActiveState')=='active' and t.prop(t.RESTORE+'.service','ActiveState')=='inactive':
        keep(t,t.DROP,t.DROP_BYTES)
        remove_own(perm,config)
        t.ctl('daemon-reload')
        original_files(t,[t.DROP])
    else:
        remove_own(perm,config)
        t.restore()
    print('GATEWAY_65R_UNCOMMITTED_CHANGE_RECOVERED')

def commit(t,perm,config):
    need((t.WORK/'TRIAL_ACTIVE_13_TOOLS').is_file() and not (t.WORK/'RESTORED_11_TOOLS').exists(),'trial_not_current')
    need(t.loaded(t.NEW) and hashlib.sha256(secure_read(t.NEW)).hexdigest()==t.PIN,'new_gateway_not_live')
    main_pid=t.prop(t.UNIT,'MainPID')
    tunnel_pid=t.prop(t.TUN,'MainPID')
    evidence(t)
    if os.path.lexists(perm):
        need(secure_read(perm)==config and not os.path.lexists(t.DROP),'partial_or_foreign_commit_inspect')
        original_files(t,[perm])
        need(t.prop(t.RESTORE+'.timer','ActiveState')=='inactive'
             and (STATE/'committed.json').is_file(),'partial_commit_inspect')
        print('GATEWAY_65R_ALREADY_COMMITTED')
        return
    timer_safe(t)
    need(secure_read(t.DROP)==t.DROP_BYTES,'trial_drop_changed')
    original_files(t,[t.DROP])
    keep(t,STATE/'gateway.sha256',(t.PIN+'  '+str(t.NEW)+'\n').encode())
    # The existing timer stays armed until the permanent definition and its live process pass.
    t.cmd(['/usr/bin/systemd-run','--user','--quiet','--unit='+RESCUE,'--on-active=110s',
           '--timer-property=AccuracySec=1s',str(t.PY),'-IB',str(STATE/'commit65r.py'),'recover'])
    need(t.prop(RESCUE+'.timer','ActiveState')=='active','commit_recovery_not_armed')
    t.publish(perm,config)
    try:
        syncdir(perm.parent)
        remove_own(t.DROP,t.DROP_BYTES)
        t.ctl('daemon-reload')
        original_files(t,[perm])
        need(str(t.NEW)+' serve' in t.prop(t.UNIT,'ExecStart') and str(STATE/'gateway.sha256') in t.prop(t.UNIT,'ExecStartPre'),
             'effective_definition_mismatch')
        need(t.loaded(t.NEW) and t.prop(t.UNIT,'MainPID')==main_pid and t.prop(t.TUN,'MainPID')==tunnel_pid,
             'service_pid_changed')
        evidence(t)
        timer_safe(t)
    except BaseException:
        # Return to the same volatile trial. Do not replace a foreign file.
        if t.prop(t.RESTORE+'.timer','ActiveState')=='active' and t.prop(t.RESTORE+'.service','ActiveState')=='inactive':
            keep(t,t.DROP,t.DROP_BYTES)
        remove_own(perm,config)
        t.ctl('daemon-reload')
        raise
    t.ctl('stop',t.RESTORE+'.timer')
    need(t.prop(t.RESTORE+'.timer','ActiveState')=='inactive' and t.prop(t.RESTORE+'.service','ActiveState')=='inactive',
         'timer_stop_unconfirmed_inspect')
    keep(t,STATE/'committed.json',json.dumps({'gateway':str(t.NEW),'source_sha256':t.PIN,'unit':t.UNIT,
        'persistent_override':str(perm),'backup':str(t.BK),'chatgpt_canary':CG,'agent_canary':t.CANARY},
        sort_keys=True).encode())
    t.ctl('stop',RESCUE+'.timer')
    print('GATEWAY_65R_COMMITTED tools=13 canaries=2 dispatch=false same_gateway_pid=true same_tunnel_pid=true trial_timer=inactive')
def rollback(t,perm,config):
    # Explicit owner maintenance action at a clean delivery boundary.
    need(not os.path.lexists(t.DROP) and t.prop(t.RESTORE+'.timer','ActiveState')=='inactive','trial_still_active')
    if os.path.lexists(perm):
        original_files(t,[perm])
        remove_own(perm,config)
    t.ctl('daemon-reload')
    t.sources()
    if not t.loaded(t.OLD):
        t.ctl('restart',t.UNIT)
    t.wait_live(t.OLD)
    t.smoke(False)
    print('GATEWAY_65R_ROLLED_BACK tools=11')
def main():
    need(os.getuid()==os.geteuid()!=0 and Path.home()==HOME,'host_identity')
    os.umask(0o077)
    t=load_trial()
    parent=HOME/'.config/systemd/user'/(t.UNIT+'.d')
    trust_chain([HOME, HOME/'.config',HOME/'.config/systemd',HOME/'.config/systemd/user',parent])
    trust_chain([HOME,BASE,STATE])
    perm=parent/t.DROP.name
    config=t.DROP_BYTES + ('ExecStartPre=/usr/bin/sha256sum --status --check '+str(STATE/'gateway.sha256')+'\n').encode()
    fd=os.open(STATE,os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX if sys.argv[1:]==['recover'] else fcntl.LOCK_EX|fcntl.LOCK_NB)
        if sys.argv[1:]==['commit']: commit(t,perm,config)
        elif sys.argv[1:]==['recover']: recover(t,perm,config)
        elif sys.argv[1:]==['rollback']: rollback(t,perm,config)
        else: raise SystemExit('expected commit or rollback')
    finally: os.close(fd)
if __name__=='__main__': main()
