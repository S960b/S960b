#!/usr/bin/env python3
"""Stage 36B: one-task, owner-controlled MultiTool managed-inbox activation.

Only local config/permit/gate modifications. Never writes PostgreSQL, invokes SDK,
closes MultiTool processes, touches SSH/tunnel/MCP services, or creates dialogue.
Prepare and commit are one-shot. Any uncertainty requires status/inspection.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
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
ROOT = HOME / 'hermes-pg-stage36b-managed-inbox-handoff'
CFG = HOME / '.config/gigatool/opencode.json'
PERMIT = HOME / '.config/bridge-pg-v2/managed-mcp-write-permit35c.json'
GATE = HOME / '.config/bridge-pg-v2/managed-writes-stage34d-armed'
GATE_BYTES = b'ALLOW_MANAGED_WRITES_AFTER_EXCLUSIVE_SDK_REVIEW\n'
CANDIDATE = HOME / 'hermes-pg-stage34eb-pilot-candidate/mt-pg-inbox-managed-stage34eb-pilot-unregistered.mjs'
CLI = HOME / 'hermes-pg-stage34d-write-adapter/managed_agent_cli34d.py'
CANARY_JS = HOME / 'hermes-pg-stage33i-ackfix/mt-pg-inbox-agent-stage33i.mjs'
CANARY = 'task_f2c64cfdf7894537b35a19a6def977c6'
OLD_PILOT = 'task_bddaadef89b7458fb7f8cf437dc0f023'
TASK = 'task_b6c26aa18220412bafdbf32393f16568'
REQUEST_ID = 'pgv2-stage36-independent-e2e-20261123-v1'
TASK_MID = 'pgv2-stage36-task-20261123-b6c26aa1-v1'
REPORT_MID = 'pgv2-mg-' + TASK[5:] + '-1-report'
REPORT_BODY = 'PGV2_STAGE36_E2E_ACK_20261123_B6C26A'
TITLE = 'Hermes PG v2 Independent E2E Stage 36 (dispatch disabled)'
CFG7_SHA = 'e5320f6e971073d5d2c43a64ce85d165cb49dc9a94e6cf7806009ebc2b59ded1'
CANDIDATE_SHA = 'b3679086583d77c96b6ed1c3dc7b1ed1656707dabba4d3ac74843fe64177d730'
CLI_SHA = 'bf8cad69a02e71a70ec3377504c24a3fc2cb71a38f2c8c4a025abc587b774485'
CANARY_SHA = '220da977562f9c0763d6b5d5640f686f54f041d357ed397c8482c0d4363f525c'
RUNTIME = HOME / 'hermes-pg-stage35c-static-guard/runtime35c.py'
RUNTIME_SHA = '07f5a6a909a954913843491a3ed750361bc4f66826144719f1aac6328d093641'
SCOPE = HOME / '.local/state/hermes-pg-stage36/scope.json'
STATE_DIR = HOME / '.local/state/mt-pg-inbox-managed-stage34e'
STATE = STATE_DIR / 'state.json'
LOCK = STATE_DIR / 'inbox.lock'
UNIT_DIR = HOME / '.config/systemd/user'
UNIT = 'hermes-pg-stage36b-gate-expiry'
SERVICE = UNIT_DIR / (UNIT+'.service')
TIMER = UNIT_DIR / (UNIT+'.timer')
PREP = ROOT / 'prepared.json'
COMMIT_ATTEMPT = ROOT / 'commit-attempt.json'
COMMITTED = ROOT / 'committed.json'
BACKUP_CFG = ROOT / 'backup-opencode.json'
BACKUP_PERMIT = ROOT / 'backup-permit.json'
DRAFT_CFG = ROOT / 'draft-opencode.json'
DRAFT_PERMIT = ROOT / 'draft-permit.json'
GATE_EXPIRY_SEC = 20*60
MIN_PERMIT_SEC = 15*60

class Stop(RuntimeError):
    pass

def need(condition, code):
    if not condition: raise Stop('STAGE36B_STOP_' + code)

def sha(raw): return hashlib.sha256(raw).hexdigest()

def secure_file(path, private=True):
    try:
        s = path.lstat()
        return (stat.S_ISREG(s.st_mode) and s.st_uid == os.geteuid() and
                s.st_nlink == 1 and not path.is_symlink() and
                (not private or not s.st_mode & 0o077))
    except OSError: return False

def safe_dir(path):
    try:
        s=path.lstat()
        return stat.S_ISDIR(s.st_mode) and s.st_uid==os.geteuid() and not path.is_symlink() and not s.st_mode & 0o022
    except OSError: return False

def checked_bytes(path, private=True):
    need(secure_file(path,private), 'UNTRUSTED_FILE_'+path.name)
    return path.read_bytes()

def atom_create(path, raw):
    need(not path.exists() and not path.is_symlink(), 'ALREADY_EXISTS_'+path.name)
    fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd,'wb') as out:
        out.write(raw); out.flush(); os.fsync(out.fileno())
    sync_dir(path.parent)

def sync_dir(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)

def atom_replace(path, raw, expected_sha):
    need(sha(checked_bytes(path)) == expected_sha, 'REPLACE_RACE_'+path.name)
    tmp=path.with_name('.'+path.name+'.stage36b-'+str(os.getpid()))
    atom_create(tmp,raw)
    try:
        need(sha(checked_bytes(path)) == expected_sha, 'REPLACE_RACE_'+path.name)
        os.replace(tmp,path); sync_dir(path.parent)
    finally:
        tmp.unlink(missing_ok=True)

def json_bytes(obj):
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',',':'))+'\n').encode('utf8')

def user_guard():
    need(os.getuid()==os.geteuid()!=0 and pwd.getpwuid(os.geteuid()).pw_name=='kali' and
         Path.home()==HOME and Path(os.environ.get('HOME',''))==HOME,
         'WRONG_USER_HOME')
    need(safe_dir(ROOT) and safe_dir(ROOT.parent) and safe_dir(PERMIT.parent), 'UNTRUSTED_WORKING_DIRECTORY')
    need(not GATE.is_symlink(), 'SYMLINK_GATE')
    need(sha(checked_bytes(CANDIDATE,private=False))==CANDIDATE_SHA,'CANDIDATE_CHANGED')
    need(sha(checked_bytes(CLI,private=False))==CLI_SHA,'CLI_CHANGED')
    need(sha(checked_bytes(CANARY_JS,private=False))==CANARY_SHA,'CANARY_JS_CHANGED')
    need(sha(checked_bytes(RUNTIME,private=False))==RUNTIME_SHA,'MCP_RUNTIME_CHANGED')
    # Runtime35c permit is byte-exact config-SHA bound; any config change must rebind it.
    source=RUNTIME.read_text('utf8')
    need("p['config_sha256']" in source and 'return tid in p[\'task_ids\']' in source,
         'MCP_RUNTIME_GUARD_SHAPE_CHANGED')
    c=CANDIDATE.read_text('utf8')
    for tok in ('const dryRun = false;', 'stage34ebPilotTaskIds(options)',
                'if (!ctx.taskIds || !ctx.taskIds.includes(dialog?.task_id)) continue;',
                'managed_agent_cli34d.py',
                'for (const taskId of Object.keys(state.inflight).sort())'):
        need(tok in c,'CANDIDATE_UNSAFE')
    need('const dryRun = true;' not in c,'CANDIDATE_FORCED_DRYRUN')

def entry_path(e):
    if isinstance(e,str):return e
    if isinstance(e,list) and len(e)==2 and isinstance(e[0],str) and isinstance(e[1],dict):return e[0]
    raise Stop('STAGE36B_STOP_UNEXPECTED_PLUGIN_SHAPE')

def build_cfg(raw):
    original=json.loads(raw)
    need(isinstance(original,dict) and isinstance(original.get('plugin'),list) and len(original['plugin'])==7,
         'EXPECTED_SEVEN_PLUGINS')
    paths=[entry_path(x) for x in original['plugin']]
    need(paths.count(str(CANARY_JS))==1 and str(CANDIDATE) not in paths,
         'CANARY_OR_CANDIDATE_CONFIG_DRIFT')
    need(not any('mt-git-inbox' in x or 'mt-pg-inbox-canary' in x for x in paths),
         'LEGACY_PLUGIN_REINTRODUCED')
    can=original['plugin'][paths.index(str(CANARY_JS))]
    need(can[1].get('dry_run') is False and can[1].get('task_ids')==[CANARY],
         'CANARY_SCOPE_CHANGED')
    obj=json.loads(raw)
    obj['plugin'].append([str(CANDIDATE),{'dry_run':False,'tick_ms':30000,'task_ids':[TASK]}])
    cooked=(json.dumps(obj,ensure_ascii=False,indent=2)+'\n').encode('utf8')
    rebuilt=json.loads(cooked)
    need(rebuilt['plugin'][:7]==original['plugin'] and len(rebuilt['plugin'])==8 and
         {k:v for k,v in rebuilt.items() if k!='plugin'}=={k:v for k,v in original.items() if k!='plugin'},
         'UNRELATED_CONFIG_DIFF')
    return cooked

def validate_permit(raw, cfg_sha, *, min_secs=0):
    p=json.loads(raw)
    need(isinstance(p,dict) and set(p)=={'version','expires_unix','config_sha256','task_ids','allow_create','static_tunnel_verified'},
         'PERMIT_SCHEMA_MISMATCH')
    need(type(p['expires_unix']) is int and p['expires_unix'] > time.time()+min_secs and
         p['expires_unix']<=time.time()+86400,
         'PERMIT_EXPIRED_OR_TOO_SHORT')
    need(p['version']==1 and p['allow_create'] is False and p['task_ids']==[TASK] and
         p['static_tunnel_verified'] is True and p['config_sha256']==cfg_sha,
         'PERMIT_SCOPE_MISMATCH')
    return p

def new_permit(old_raw,new_cfg_sha):
    p=validate_permit(old_raw,CFG7_SHA,min_secs=MIN_PERMIT_SEC)
    p['config_sha256']=new_cfg_sha  # no permission widening, no expiry extension
    cooked=json_bytes(p)
    validate_permit(cooked,new_cfg_sha,min_secs=MIN_PERMIT_SEC)
    return cooked

def state_safe():
    need(safe_dir(STATE_DIR),'MANAGED_STATE_DIR_UNTRUSTED')
    need(not LOCK.exists() and not LOCK.is_symlink(),'MANAGED_PLUGIN_LOCK_ALREADY_PRESENT')
    if STATE.exists() or STATE.is_symlink():
        state=json.loads(checked_bytes(STATE))
        need(isinstance(state,dict) and state.get('inflight',{})=={} and
             not state.get('seen',{}).get(TASK) and isinstance(state.get('seen',{}),dict),
             'MANAGED_INFLIGHT_OR_CURSOR_ALREADY_PRESENT')

def service_active(name, yes=True):
    r=subprocess.run(['systemctl','--user','is-active','--quiet',name],capture_output=True,timeout=10)
    need((r.returncode==0)==yes,'SERVICE_STATE_'+name.split('.')[0])

def runtime_health():
    for url in ('http://127.0.0.1:18777/ping','http://127.0.0.1:18778/readyz'):
        try:
            with urllib.request.urlopen(url,timeout=5) as r: good=r.status==200
        except Exception: good=False
        need(good,'LOCAL_HEALTH_FAILED')

def db_snapshot():
    sys.path.insert(0,str(HOME/'hermes-pg-stage33e-shadow-clients'))
    from actor_import33e import actor_classes
    ChatGPT,Agent=actor_classes()
    cg,ag=ChatGPT(),Agent()
    with cg._connect() as con:
        with con.cursor() as cur:
            cur.execute('SET TRANSACTION READ ONLY')
            cur.execute('SELECT session_user AS actor')
            need(cur.fetchone()['actor']=='bridge_chatgpt','WRONG_CG_ROLE')
            cur.execute('SELECT bridge.cg_list_managed_dialogs(%s,%s) AS result',(0,50))
            listing=cur.fetchone()['result']
            need(isinstance(listing,list) and len(listing)==2 and {a['task_id'] for a in listing}=={TASK,OLD_PILOT},
                 'REGISTRY_NOT_TWO_PINNED_DIALOGS')
            cur.execute('SELECT bridge.cg_managed_detail(%s) AS result',(TASK,))
            d=cur.fetchone()['result']
            need(isinstance(d,dict) and d.get('registered') is True and d.get('state')=='active' and
                 d.get('title')==TITLE and d.get('version')==1 and d.get('last_seq')==1 and
                 d.get('next_actor')=='agent' and d.get('policy_version')==1 and
                 d.get('dispatch_enabled') is True,
                 'NEW_TASK_NOT_DISPATCHABLE_STATE')
            cur.execute('SELECT bridge.cg_view_managed_dialog(%s,%s,%s) AS result',(TASK,0,3))
            history=cur.fetchone()['result']
            msgs=history.get('messages') if isinstance(history,dict) else None
            need(isinstance(msgs,list) and len(msgs)==1 and msgs[0].get('seq')==1 and
                 msgs[0].get('message_id')==TASK_MID and msgs[0].get('sender')=='chatgpt' and
                 msgs[0].get('recipient')=='agent' and msgs[0].get('kind')=='task' and
                 REPORT_BODY in msgs[0].get('body',''), 'FIRST_TASK_CHANGED')
            cur.execute('SELECT bridge.delivery_status(%s,%s) AS result',(TASK,1))
            delivery=cur.fetchone()['result']
            need(isinstance(delivery,dict) and delivery.get('status')=='pending' and
                 delivery.get('attempts')==0,'TASK_ALREADY_CLAIMED')
            cur.execute('SELECT bridge.cg_managed_detail(%s) AS result',(OLD_PILOT,))
            old=cur.fetchone()['result']
            need(isinstance(old,dict) and old.get('dispatch_enabled') is False and
                 old.get('last_seq')==2 and old.get('policy_version')==2,
                 'OLD_PILOT_CHANGED')
            cur.execute('SELECT bridge.dialog_metadata(%s) AS result',(CANARY,))
            can=cur.fetchone()['result']
            need(isinstance(can,dict) and can.get('last_seq')==6 and can.get('state')=='active',
                 'CANARY_DIALOG_CHANGED')
            for seq in (5,6):
                cur.execute('SELECT bridge.delivery_status(%s,%s) AS result',(CANARY,seq))
                need(cur.fetchone()['result'].get('status')=='delivered','CANARY_DELIVERY_CHANGED')
    with ag._connect() as con:
        with con.cursor() as cur:
            cur.execute('SET TRANSACTION READ ONLY')
            cur.execute('SELECT session_user AS actor')
            need(cur.fetchone()['actor']=='bridge_agent','WRONG_AGENT_ROLE')
            cur.execute('SELECT bridge.ag_dispatch_list(%s,%s) AS result',(0,50))
            available=cur.fetchone()['result']
            need(isinstance(available,list) and len(available)==1 and available[0].get('task_id')==TASK,
                 'MANAGED_DISPATCH_UNEXPECTED')
    return d

def mt_processes():
    found=[];unknown=[]
    for d in Path('/proc').iterdir():
        if not d.name.isdigit():continue
        try:
            if d.stat().st_uid!=os.geteuid():continue
            comm=(d/'comm').read_text('utf8').strip().lower()
            candidate=any(x in comm for x in ('multitool','gigatool','opencode'))
            if not candidate and comm in ('node','bun','electron'):
                try:
                    cmd=(d/'cmdline').read_bytes().lower()
                except OSError:unknown.append(d.name);continue
                candidate=any(x in cmd for x in (b'multitool',b'gigatool',b'opencode'))
            if candidate:found.append(d.name)
        except FileNotFoundError:continue
        except OSError:unknown.append(d.name)
    return found,unknown

def check_quiet():
    found,unknown=mt_processes()
    need(not found and not unknown,'MULTITOOL_STILL_RUNNING_CLOSE_ALL_WINDOWS')
    time.sleep(3)
    found,unknown=mt_processes()
    need(not found and not unknown,'MULTITOOL_REAPPEARED')

def current_guards(*,cfg_sha=CFG7_SHA,require_gate_absent=True,min_permit=MIN_PERMIT_SEC):
    user_guard()
    need(sha(checked_bytes(CFG))==cfg_sha,'CONFIG_SHA_MISMATCH')
    validate_permit(checked_bytes(PERMIT),cfg_sha,min_secs=min_permit)
    if require_gate_absent:
        need(not GATE.exists() and not GATE.is_symlink(),'MANAGED_CLI_GATE_ALREADY_ARMED')
    need(sha(checked_bytes(SCOPE))!='','INVALID_SCOPE')
    scope=json.loads(SCOPE.read_text('utf8'))
    need(scope.get('task_id')==TASK and scope.get('phase')=='scoped','STAGE36_SCOPE_MARKER_CHANGED')
    state_safe()
    service_active('hermes-pg-tunnel-stage35jk.service')
    service_active('hermes-pg-tunnel-stage33fd.service',False)
    service_active('hermes-pg-stage35c-static-shadow.service')
    runtime_health()
    db_snapshot()
    return True

def prepared_files():
    meta=json.loads(checked_bytes(PREP))
    files={'cfg7':BACKUP_CFG,'permit7':BACKUP_PERMIT,'cfg8':DRAFT_CFG,'permit8':DRAFT_PERMIT}
    for key,path in files.items():
        need(sha(checked_bytes(path))==meta[key+'_sha'],'PREPARED_FILE_CHANGED_'+key)
    need(meta.get('task_id')==TASK and meta.get('config_baseline_sha')==CFG7_SHA,
         'PREPARATION_CONTEXT_CHANGED')
    need(sha(checked_bytes(DRAFT_CFG))==sha(build_cfg(checked_bytes(BACKUP_CFG))),
         'PROPOSED_PLUGIN_CONFIG_CHANGED')
    need(checked_bytes(DRAFT_PERMIT)==new_permit(checked_bytes(BACKUP_PERMIT),meta['cfg8_sha']),
         'PROPOSED_PERMIT_CHANGED')
    return meta

def prepare():
    current_guards()
    for p in (PREP,COMMIT_ATTEMPT,COMMITTED,BACKUP_CFG,BACKUP_PERMIT,DRAFT_CFG,DRAFT_PERMIT,SERVICE,TIMER):
        need(not p.exists() and not p.is_symlink(),'PREPARATION_ALREADY_EXISTS_'+p.name)
    old_cfg=checked_bytes(CFG)
    old_permit=checked_bytes(PERMIT)
    new_cfg=build_cfg(old_cfg)
    newpermit=new_permit(old_permit,sha(new_cfg))
    for path,bytes_ in ((BACKUP_CFG,old_cfg),(BACKUP_PERMIT,old_permit),
                        (DRAFT_CFG,new_cfg),(DRAFT_PERMIT,newpermit)):
        atom_create(path,bytes_)
    meta={'stage':'36B','task_id':TASK,'config_baseline_sha':CFG7_SHA,
          'cfg7_sha':sha(old_cfg),'permit7_sha':sha(old_permit),
          'cfg8_sha':sha(new_cfg),'permit8_sha':sha(newpermit),
          'prepared_at':int(time.time())}
    atom_create(PREP,json_bytes(meta))
    print('STAGE36B_PRIVATE_BACKUPS_AND_EIGHT_PLUGIN_DRAFT_READY')
    print('STAGE36B_CANARY_UNCHANGED_MANAGED_SCOPED_ONE_TASK')
    print('STAGE36B_DISPATCH_ALREADY_TRUE_TASK_PENDING_NOT_CLAIMED')
    print('STAGE36B_MCP_PERMIT_REBIND_PREPARED_NO_EXPIRY_EXTENSION')
    print('STAGE36B_PREPARE_COMPLETE_NO_LIVE_WRITES')

def service_content(now=None):
    if now is None: now=datetime.now(timezone.utc)
    expiry=(now + timedelta(seconds=GATE_EXPIRY_SEC)).strftime("%Y-%m-%d %H:%M:%S UTC")
    return (('[Unit]\nDescription=Revoke temporary Stage36B managed CLI write gate\n'
            '[Service]\nType=oneshot\n'
            'ExecStart=/usr/bin/python3 -IB '+str(ROOT/'stage36b.py')+' revoke-gate\n'
            'NoNewPrivileges=true\n').encode(),
            ('[Unit]\nDescription=Automatically revoke Stage36B CLI write gate\n'
             '[Timer]\nOnCalendar='+expiry+'\nPersistent=true\nAccuracySec=1s\nUnit='+UNIT+'.service\n'
             '[Install]\nWantedBy=timers.target\n').encode())

def install_expiry():
    need(safe_dir(UNIT_DIR),'SYSTEMD_UNIT_DIR_UNSAFE')
    svc,timer=service_content()
    atom_create(SERVICE,svc)
    atom_create(TIMER,timer)
    subprocess.run(['systemctl','--user','daemon-reload'],check=True,timeout=12,capture_output=True)
    subprocess.run(['systemctl','--user','enable','--now',UNIT+'.timer'],check=True,timeout=12,capture_output=True)
    service_active(UNIT+'.timer')
    print('STAGE36B_MANAGED_CLI_GATE_PERSISTENT_AUTO_REVOKE_TIMER_ARMED_20_MINUTES')

def commit():
    need(not COMMIT_ATTEMPT.exists() and not COMMIT_ATTEMPT.is_symlink(),
         'COMMIT_ALREADY_ATTEMPTED_DO_NOT_RETRY')
    meta=prepared_files()
    current_guards()
    need(checked_bytes(CFG)==checked_bytes(BACKUP_CFG) and checked_bytes(PERMIT)==checked_bytes(BACKUP_PERMIT),
         'LIVE_FILES_CHANGED_SINCE_PREPARE')
    check_quiet()
    current_guards()
    check_quiet()
    atom_create(COMMIT_ATTEMPT,json_bytes({'stage':36,'phase':'commit_attempted',
                                           'task_id':TASK,'at_unix':int(time.time())}))
    print('STAGE36B_COMMIT_ONCE_JOURNALED_NO_RETRY',flush=True)
    install_expiry()  # fail before touching live config/permit/gate
    need(not GATE.exists() and not GATE.is_symlink(),'CLI_GATE_CHANGED_BEFORE_COMMIT')
    check_quiet()
    atom_replace(CFG,checked_bytes(DRAFT_CFG),meta['cfg7_sha'])
    atom_replace(PERMIT,checked_bytes(DRAFT_PERMIT),meta['permit7_sha'])
    need(sha(checked_bytes(CFG))==meta['cfg8_sha'] and
         sha(checked_bytes(PERMIT))==meta['permit8_sha'],
         'COMMIT_FILE_MISMATCH_CHECK_STATUS')
    atom_create(GATE,GATE_BYTES)
    need(checked_bytes(GATE)==GATE_BYTES,'CLI_GATE_BAD_AFTER_INSTALL')
    atom_create(COMMITTED,json_bytes({'task_id':TASK,'cfg8_sha':meta['cfg8_sha'],
                                      'permit8_sha':meta['permit8_sha'],'committed_at':int(time.time())}))
    print('STAGE36B_EIGHTH_MANAGED_PLUGIN_REGISTERED_ONE_TASK_ONLY')
    print('STAGE36B_MCP_PERMIT_REBOUND_EXACT_TASK_NO_EXPIRY_EXTENSION')
    print('STAGE36B_AGENT_REPORT_GATE_ARMED_AUTO_REVOKE_20_MINUTES')
    print('STAGE36B_NOW_OPEN_MULTITOOL_TO_PROCESS_EXISTING_SEQ1_ONCE')

def revoke_gate():
    need(os.getuid()==os.geteuid()!=0 and pwd.getpwuid(os.geteuid()).pw_name=='kali',
         'REVOKE_WRONG_USER')
    if not GATE.exists() and not GATE.is_symlink():
        print('STAGE36B_CLI_GATE_ALREADY_ABSENT')
        return
    need(secure_file(GATE) and checked_bytes(GATE)==GATE_BYTES and
         stat.S_IMODE(GATE.stat().st_mode)==0o600,
         'UNRECOGNIZED_CLI_GATE_NO_DELETE')
    GATE.unlink()
    sync_dir(GATE.parent)
    print('STAGE36B_MANAGED_CLI_GATE_REVOKED')

def status():
    # This mode never reveals secrets or message bodies; works after permit expiry.
    user_guard()
    cfgsha=sha(checked_bytes(CFG))
    print('STAGE36B_STATUS_CONFIG_SEVEN',cfgsha==CFG7_SHA)
    print('STAGE36B_STATUS_PREPARED',PREP.exists())
    print('STAGE36B_STATUS_COMMIT_ATTEMPTED',COMMIT_ATTEMPT.exists())
    print('STAGE36B_STATUS_COMMITTED',COMMITTED.exists())
    if PREP.exists() and secure_file(PREP):
        meta=json.loads(PREP.read_text())
        print('STAGE36B_STATUS_CONFIG_EIGHT',cfgsha==meta['cfg8_sha'])
        if PERMIT.exists() and secure_file(PERMIT):
            ps=sha(checked_bytes(PERMIT))
            print('STAGE36B_STATUS_PERMIT_REBOUND',ps==meta['permit8_sha'])
            try:
                p=json.loads(PERMIT.read_text())
                print('STAGE36B_STATUS_PERMIT_SECONDS_LEFT',max(0,p['expires_unix']-int(time.time())))
            except (TypeError,ValueError,KeyError): print('STAGE36B_STATUS_PERMIT_UNKNOWN')
    print('STAGE36B_STATUS_CLI_GATE', 'PRESENT' if GATE.exists() else 'ABSENT')
    print('STAGE36B_STATUS_MT_PROCESSES',len(mt_processes()[0]))
    print('STAGE36B_STATUS_MT_UNKNOWN_PROCESSES',len(mt_processes()[1]))
    if STATE.exists() and secure_file(STATE):
        o=json.loads(STATE.read_text())
        print('STAGE36B_STATUS_INFLIGHT', bool(o.get('inflight')))
        print('STAGE36B_STATUS_NEW_SEEN',o.get('seen',{}).get(TASK,0))
    print('STAGE36B_STATUS_READONLY_NO_DB_MUTATION')


def progress():
    """Read-only PG and local state; output safe, fixed-classification markers."""
    user_guard()
    sys.path.insert(0,str(HOME/'hermes-pg-stage33e-shadow-clients'))
    from actor_import33e import actor_classes
    ChatGPT,_=actor_classes()
    cg=ChatGPT()
    d=cg._one('SELECT bridge.cg_managed_detail(%s) AS result',(TASK,))
    need(isinstance(d,dict) and d.get('registered') is True,'PROGRESS_TASK_MISSING')
    print('STAGE36B_PROGRESS_DIALOG_VERSION',d.get('version'))
    print('STAGE36B_PROGRESS_LAST_SEQ',d.get('last_seq'))
    print('STAGE36B_PROGRESS_DISPATCH_ENABLED',d.get('dispatch_enabled'))
    t=cg.delivery_status(TASK,1)
    status=t.get('status') if isinstance(t,dict) else 'unknown'
    print('STAGE36B_PROGRESS_TASK_DELIVERY_'+str(status).upper() if status in ('pending','leased','delivered') else 'STAGE36B_PROGRESS_TASK_DELIVERY_UNKNOWN')
    page=cg._one('SELECT bridge.cg_view_managed_dialog(%s,%s,%s) AS result',(TASK,0,4))
    messages=page.get('messages',[]) if isinstance(page,dict) else []
    report=[m for m in messages if m.get('seq')==2 and m.get('kind')=='report' and
            m.get('sender')=='agent' and m.get('recipient')=='chatgpt']
    if len(report)==1:
        m=report[0]
        exact=(m.get('message_id')==REPORT_MID and m.get('body')==REPORT_BODY)
        print('STAGE36B_PROGRESS_REPORT_SEQ2_PRESENT')
        print('STAGE36B_PROGRESS_REPORT_EXACT_'+('YES' if exact else 'NO'))
        r=cg.delivery_status(TASK,2)
        st=r.get('status') if isinstance(r,dict) else 'unknown'
        print('STAGE36B_PROGRESS_REPORT_DELIVERY_'+str(st).upper() if st in ('pending','leased','delivered') else 'STAGE36B_PROGRESS_REPORT_DELIVERY_UNKNOWN')
    else:
        print('STAGE36B_PROGRESS_REPORT_SEQ2_ABSENT')
    print('STAGE36B_PROGRESS_READONLY_NO_DB_MUTATION')

def rollback_before_dispatch():
    """Explicit operator-only, never safe after a claim or report."""
    meta=prepared_files()
    need(COMMIT_ATTEMPT.exists(),'ROLLBACK_NOT_ACTIVATED')
    check_quiet()
    db_snapshot()    # requires pending, attempts=0, no report
    state_safe()     # no durable inflight
    cursha=sha(checked_bytes(CFG))
    need(cursha in (meta['cfg7_sha'],meta['cfg8_sha']),'UNKNOWN_CONFIG_NO_ROLLBACK')
    revoke_gate()
    if cursha==meta['cfg8_sha']:
        atom_replace(CFG,checked_bytes(BACKUP_CFG),meta['cfg8_sha'])
    psha=sha(checked_bytes(PERMIT))
    need(psha in (meta['permit7_sha'],meta['permit8_sha']), 'UNKNOWN_PERMIT_NO_ROLLBACK')
    if psha==meta['permit8_sha']:
        atom_replace(PERMIT,checked_bytes(BACKUP_PERMIT),meta['permit8_sha'])
    print('STAGE36B_ROLLBACK_SEVEN_PLUGIN_CONFIG_AND_SCOPED_PERMIT_RESTORED')
    print('STAGE36B_DISPATCH_STILL_TRUE_TASK_STILL_PENDING')

def main():
    try:
        need(len(sys.argv)==2 and sys.argv[1] in
             ('prepare','commit','status','progress','revoke-gate','rollback-before-dispatch'),
             'USAGE')
        name=sys.argv[1]
        {'prepare':prepare,'commit':commit,'status':status,'revoke-gate':revoke_gate,
         'rollback-before-dispatch':rollback_before_dispatch,'progress':progress}[name]()
        return 0
    except Exception as exc:
        code=str(exc) if isinstance(exc,Stop) else type(exc).__name__
        print(str(code)[:150] if str(code).startswith('STAGE36B_STOP_') else 'STAGE36B_STOP_'+str(code)[:120],
              file=sys.stderr,flush=True)
        return 2

if __name__ == '__main__': raise SystemExit(main())
