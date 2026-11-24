#!/usr/bin/env python3
"""Stage37B1: conservative, read-only MultiTool restart durability check.

Does not issue permits, dispatch, claim/ack, send tasks, change systemd, or
stop/start MultiTool. Operator closes and reopens MultiTool explicitly.
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
import time
from contextlib import contextmanager
from pathlib import Path

HOME = Path('/home/kali')
ROOT = HOME / 'hermes-pg-stage37b-readonly-restart'
CFG = HOME / '.config/gigatool/opencode.json'
CAP_DIR = HOME / '.config/bridge-pg-v2'
PERMIT = CAP_DIR / 'managed-mcp-write-permit35c.json'
GATE = CAP_DIR / 'managed-writes-stage34d-armed'
WATCH_TIMER = 'hermes-pg-stage37a-permit-watchdog.timer'
CANARY = 'task_f2c64cfdf7894537b35a19a6def977c6'
PILOT = 'task_bddaadef89b7458fb7f8cf437dc0f023'
TASK = 'task_b6c26aa18220412bafdbf32393f16568'
TASK_MID = 'pgv2-stage36-task-20261123-b6c26aa1-v1'
REPORT_MID = 'pgv2-mg-b6c26aa18220412bafdbf32393f16568-1-report'
REPORT = 'PGV2_STAGE36_E2E_ACK_20261123_B6C26A'
PLUGIN = HOME / 'hermes-pg-stage34eb-pilot-candidate/mt-pg-inbox-managed-stage34eb-pilot-unregistered.mjs'
CANARY_JS = HOME / 'hermes-pg-stage33i-ackfix/mt-pg-inbox-agent-stage33i.mjs'
STATE = HOME / '.local/state/mt-pg-inbox-managed-stage34e/state.json'
LOCK = STATE.parent / 'inbox.lock'
STAGE36B = HOME / 'hermes-pg-stage36b-managed-inbox-handoff/prepared.json'
BASELINE = ROOT / 'baseline.json'
QUIESCED = ROOT / 'quiesced.json'
AFTER = ROOT / 'after.json'
LOCK_FILE = ROOT / '.stage37b.lock'

class Stop(RuntimeError):
    pass

def need(condition: bool, code: str) -> None:
    if not condition:
        raise Stop('STAGE37B_STOP_' + code)

def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()

def trusted_dir(p: Path) -> bool:
    try:
        s = p.lstat()
        return stat.S_ISDIR(s.st_mode) and s.st_uid == os.geteuid() and not (s.st_mode & 0o022)
    except OSError:
        return False

def private_file_bytes(p: Path, limit: int = 2_097_152) -> bytes:
    st=p.lstat()
    need(stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid() and
         st.st_nlink==1 and not (st.st_mode & 0o077) and 0 < st.st_size <= limit,
         'UNTRUSTED_PRIVATE_FILE_'+p.name.replace('.', '_'))
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        fst=os.fstat(fd)
        need((fst.st_ino,fst.st_dev)==(st.st_ino,st.st_dev),'FILE_CHANGED_DURING_OPEN')
        raw=os.read(fd,limit+1)
        need(len(raw)<=limit,'FILE_TOO_LARGE')
        return raw
    finally:
        os.close(fd)

def user_check() -> None:
    need(os.getuid()==os.geteuid()!=0 and pwd.getpwuid(os.getuid()).pw_name=='kali' and
         Path.home()==HOME and Path(os.environ.get('HOME',''))==HOME,
         'WRONG_USER_HOME')
    need(trusted_dir(ROOT) and trusted_dir(CAP_DIR),'UNTRUSTED_WORK_DIRECTORY')

def absent_active_capabilities() -> None:
    # Treat a symlink or an unrecognized file as an active capability, never silently accept.
    need(not PERMIT.exists() and not PERMIT.is_symlink() and
         not GATE.exists() and not GATE.is_symlink(), 'WRITE_CAPABILITY_PRESENT')

def config_snapshot() -> str:
    raw=private_file_bytes(CFG)
    config=json.loads(raw)
    entries=config.get('plugin')
    need(isinstance(entries,list) and len(entries)==8,'CONFIG_NOT_EIGHT_PLUGINS')
    mapped=[]
    for e in entries:
        if isinstance(e,str):
            mapped.append((e,{}))
        else:
            need(isinstance(e,list) and len(e)==2 and isinstance(e[0],str) and isinstance(e[1],dict),
                 'CONFIG_UNEXPECTED_PLUGIN_SHAPE')
            mapped.append((e[0],e[1]))
    managed=[o for p,o in mapped if p==str(PLUGIN)]
    canary=[o for p,o in mapped if p==str(CANARY_JS)]
    need(len(managed)==1 and len(canary)==1, 'EXPECTED_MANAGED_AND_CANARY_ONCE')
    need(managed[0].get('task_ids')==[TASK] and managed[0].get('dry_run') is False and
         managed[0].get('tick_ms')==30000,'MANAGED_PLUGIN_SCOPE_CHANGED')
    need(canary[0].get('task_ids')==[CANARY] and canary[0].get('dry_run') is False,
         'CANARY_SCOPE_CHANGED')
    need(not any('mt-git-inbox' in p for p,o in mapped),'LEGACY_PLUGIN_REINTRODUCED')
    prepared=json.loads(private_file_bytes(STAGE36B,8192))
    need(prepared.get('cfg8_sha')==digest(raw),'CONFIG_CHANGED_FROM_36B')
    return digest(raw)

def state_snapshot() -> dict:
    need(trusted_dir(STATE.parent),'UNTRUSTED_STATE_DIR')
    payload=json.loads(private_file_bytes(STATE,1_048_576))
    need(isinstance(payload,dict) and isinstance(payload.get('seen'),dict) and
         isinstance(payload.get('inflight'),dict),'BAD_MANAGED_STATE')
    need(payload['inflight']=={},'UNRESOLVED_INFLIGHT_NEEDS_MANUAL_REVIEW')
    seen=payload['seen'].get(TASK,0)
    need(type(seen) is int and seen>=1,'UNCOMMITTED_LOCAL_CURSOR')
    return {'seen':seen,'inflight_empty':True}

def systemd_timer_active() -> bool:
    cp=subprocess.run(['systemctl','--user','is-active','--quiet',WATCH_TIMER],
                      stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
    return cp.returncode==0

def process_start_time(raw: bytes) -> int:
    # /proc/<pid>/stat: comm may have spaces or parentheses. Fields after final ')'
    # starttime is field 22, relative index 19 from field 3 (state).
    text=raw.decode('ascii',errors='replace')
    cut=text.rfind(')')
    need(cut>=0,'PROC_STAT_INVALID')
    fields=text[cut+2:].split()
    need(len(fields)>19 and fields[19].isdigit(),'PROC_STAT_BAD_STARTTIME')
    return int(fields[19])

def multitool_processes() -> tuple[list[list[int]],int]:
    found=[];unknown=0
    for p in Path('/proc').iterdir():
        if not p.name.isdecimal():
            continue
        try:
            if p.stat().st_uid != os.geteuid():
                continue
            comm=(p/'comm').read_text('utf8').strip().lower()
            candidate=any(x in comm for x in ('multitool','gigatool','opencode'))
            if not candidate and comm in ('node','bun','electron'):
                cmd=(p/'cmdline').read_bytes().lower()
                candidate=any(x in cmd for x in (b'multitool',b'gigatool',b'opencode'))
            if candidate:
                found.append([int(p.name),process_start_time((p/'stat').read_bytes())])
        except FileNotFoundError:
            continue
        except (OSError,UnicodeError,Stop):
            unknown+=1
    found.sort()
    return found,unknown

def pg_snapshot() -> dict:
    # Read-only SQL using existing pinned read actor. No use of ag_* or write SDK.
    sys.path.insert(0,str(HOME/'hermes-pg-stage33e-shadow-clients'))
    from actor_import33e import actor_classes
    ChatGPT,_=actor_classes()
    actor=ChatGPT()
    with actor._connect() as conn:
        with conn.cursor() as cur:
            cur.execute('SET TRANSACTION READ ONLY')
            cur.execute('SELECT session_user AS actor')
            need(cur.fetchone()['actor']=='bridge_chatgpt','WRONG_DB_ROLE')
            cur.execute('SELECT bridge.cg_managed_detail(%s) AS result',(TASK,))
            d=cur.fetchone()['result']
            need(isinstance(d,dict) and d.get('registered') is True and
                 d.get('version')==2 and d.get('last_seq')==2 and
                 d.get('policy_version')==2 and d.get('next_actor')=='chatgpt' and
                 d.get('dispatch_enabled') is False,'MANAGED_DB_CHANGED')
            cur.execute('SELECT bridge.cg_view_managed_dialog(%s,%s,%s) AS result',(TASK,0,3))
            h=cur.fetchone()['result']
            messages=h.get('messages',[]) if isinstance(h,dict) else []
            need(isinstance(messages,list) and len(messages)==2,'MANAGED_REPORT_COUNT_CHANGED')
            report=next((m for m in messages if m.get('seq')==2),{})
            task=next((m for m in messages if m.get('seq')==1),{})
            need(report.get('message_id')==REPORT_MID and report.get('body')==REPORT and
                 report.get('kind')=='report' and report.get('sender')=='agent' and
                 report.get('recipient')=='chatgpt','REPORT_MUTATED_OR_DUPLICATED')
            need(task.get('message_id')==TASK_MID and task.get('kind')=='task' and
                 task.get('sender')=='chatgpt' and task.get('recipient')=='agent',
                 'TASK_IDENTITY_CHANGED')
            results=[]
            for seq in (1,2):
                cur.execute('SELECT bridge.delivery_status(%s,%s) AS result',(TASK,seq))
                receipt=cur.fetchone()['result']
                need(isinstance(receipt,dict) and receipt.get('status')=='delivered' and
                     receipt.get('attempts')==1,'RECEIPT_CHANGED_'+str(seq))
                results.append([seq,receipt.get('status'),receipt.get('attempts')])
            cur.execute('SELECT bridge.cg_managed_detail(%s) AS result',(PILOT,))
            pilot=cur.fetchone()['result']
            need(isinstance(pilot,dict) and pilot.get('last_seq')==2 and
                 pilot.get('dispatch_enabled') is False,'OLD_PILOT_CHANGED')
        conn.rollback()
    return {'version':2,'last_seq':2,'policy_version':2,'dispatch_enabled':False,
            'report_sha256':digest(REPORT.encode('utf8')),'receipts':results}

@contextmanager
def mutex():
    fd=os.open(LOCK_FILE,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        st=os.fstat(fd)
        need(stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid() and
             st.st_nlink==1 and not(st.st_mode & 0o077),'UNSAFE_LOCK_FILE')
        fcntl.flock(fd,fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd,fcntl.LOCK_UN)
        os.close(fd)

def create_once(p: Path,obj: dict) -> None:
    raw=(json.dumps(obj,sort_keys=True,separators=(',',':'))+'\n').encode('utf8')
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    dfd=os.open(ROOT,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(dfd)
    finally:os.close(dfd)

def read_marker(p:Path) -> dict:
    return json.loads(private_file_bytes(p,8192))

def current_snapshot() -> dict:
    user_check()
    absent_active_capabilities()
    need(systemd_timer_active(),'WATCHDOG_NOT_ACTIVE')
    cfg=config_snapshot()
    local=state_snapshot()
    remote=pg_snapshot()
    return {'config_sha256':cfg,'state':local,'db':remote,'epoch':int(time.time())}

def compare(snapshot:dict,base:dict)->None:
    need(snapshot['config_sha256']==base['config_sha256'],'CONFIG_DRIFT')
    need(snapshot['state']==base['state'],'LOCAL_STATE_CHANGED')
    need(snapshot['db']==base['db'],'DB_STATE_CHANGED')

def before()->None:
    with mutex():
        need(not BASELINE.exists() and not BASELINE.is_symlink(), 'BASELINE_ALREADY_EXISTS')
        need(not QUIESCED.exists() and not AFTER.exists(),'STALE_CHECKPOINT_EXISTS')
        snap=current_snapshot()
        processes,unknown=multitool_processes()
        need(unknown==0 and len(processes)>0,'MULTITOOL_NOT_RUNNING_OR_UNKNOWN')
        snap['pre_processes']=processes
        snap['phase']='pre_restart'
        create_once(BASELINE,snap)
        print('STAGE37B1_PRE_SNAPSHOT_SAVED_PRIVATE')
        print('STAGE37B1_PRE_MT_PROCESS_COUNT',len(processes))
        print('STAGE37B1_PRE_DB_SEQ1_SEQ2_DELIVERED_ONCE')
        print('STAGE37B1_PRE_LOCAL_CURSOR_SEEN',snap['state']['seen'])
        print('STAGE37B1_NOW_CLOSE_ALL_MULTITOOL_WINDOWS_AND_CONFIRM_QUIESCENCE')

def quiet()->None:
    with mutex():
        base=read_marker(BASELINE)
        need(base.get('phase')=='pre_restart','MISSING_VALID_BASELINE')
        need(not QUIESCED.exists() and not QUIESCED.is_symlink(),'QUIESCENCE_ALREADY_CONFIRMED')
        for _ in range(2):
            p,unknown=multitool_processes()
            need(not p and unknown==0,'MULTITOOL_STILL_RUNNING_OR_UNKNOWN')
            time.sleep(2)
        snap=current_snapshot()
        compare(snap,base)
        need(not LOCK.exists() and not LOCK.is_symlink(),'MANAGED_INBOX_LOCK_STILL_PRESENT')
        create_once(QUIESCED,{'phase':'quiesced','config_sha256':snap['config_sha256'],
                              'timestamp':int(time.time()),'pre_processes':base['pre_processes']})
        print('STAGE37B1_MT_FULLY_QUIESCED_STABLE')
        print('STAGE37B1_DB_AND_LOCAL_STATE_UNCHANGED_DURING_CLOSE')
        print('STAGE37B1_NOW_REOPEN_ONE_MULTITOOL_INSTANCE_AND_CHECK_AFTER')

def after()->None:
    with mutex():
        base=read_marker(BASELINE)
        quieted=read_marker(QUIESCED)
        need(quieted.get('phase')=='quiesced' and
             quieted.get('config_sha256')==base.get('config_sha256'),
             'QUIESCENCE_NOT_CONFIRMED')
        need(not AFTER.exists() and not AFTER.is_symlink(),'POST_ALREADY_CHECKED')
        processes,unknown=multitool_processes()
        need(processes and unknown==0,'MULTITOOL_NOT_REOPENED_OR_UNKNOWN')
        old={tuple(x) for x in base['pre_processes']}
        need(all(tuple(x) not in old for x in processes),'OLD_MULTITOOL_PROCESS_SURVIVED')
        snap=current_snapshot()
        compare(snap,base)
        create_once(AFTER,{'phase':'post_restart','epoch':int(time.time()),
                           'new_processes':len(processes),'state':snap['state'],
                           'db':snap['db'],'config_sha256':snap['config_sha256']})
        print('STAGE37B1_NEW_MULTITOOL_PROCESSES_CONFIRMED',len(processes))
        print('STAGE37B1_DB_SEQ1_SEQ2_DELIVERED_ONCE_NO_DUPLICATES')
        print('STAGE37B1_LOCAL_SEEN_AND_EMPTY_INFLIGHT_PRESERVED')
        print('STAGE37B1_NO_WRITE_CAPABILITIES_AFTER_RESTART')
        print('STAGE37B1_READONLY_RESTART_BASELINE_PASS')

def status()->None:
    user_check()
    print('STAGE37B1_PHASE_BASELINE_EXISTS',BASELINE.exists() and not BASELINE.is_symlink())
    print('STAGE37B1_PHASE_QUIESCED_EXISTS',QUIESCED.exists() and not QUIESCED.is_symlink())
    print('STAGE37B1_PHASE_AFTER_EXISTS',AFTER.exists() and not AFTER.is_symlink())
    absent_active_capabilities()
    print('STAGE37B1_NO_ACTIVE_CAPABILITIES')
    processes,unknown=multitool_processes()
    print('STAGE37B1_MT_PROCESSES',len(processes))
    print('STAGE37B1_MT_UNKNOWN_PROCESSES',unknown)
    print('STAGE37B1_NO_CONFIG_OR_DB_MUTATION')

def main()->int:
    try:
        user_check()
        need(len(sys.argv)==2 and sys.argv[1] in ('before','quiet','after','status'),'USAGE')
        {'before':before,'quiet':quiet,'after':after,'status':status}[sys.argv[1]]()
        return 0
    except Exception as e:
        msg=str(e) if isinstance(e,Stop) else 'STAGE37B_STOP_'+type(e).__name__
        print(msg,file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main())
