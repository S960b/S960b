#!/usr/bin/env python3
"""Stage 34G: close the completed managed pilot, then retire ONLY its MT plugin.

Two separate operations. Phase A disables one managed policy through the
bridge_chatgpt actor, verifies both receipts, and revokes one exact private
write-gate. Phase B (explicit owner arm) waits until *all* MultiTool processes
are absent, then atomically restores the byte-exact pre-pilot seven-plugin
configuration. Never calls the SDK, Git, broad SQL writers, or the MCP.
"""
from __future__ import annotations
import hashlib
import json
import os
import pwd
import stat
import subprocess
import sys
import time
from pathlib import Path

HOME = Path('/home/kali')
ROOT = HOME / 'hermes-pg-stage34g-quiesce-and-retire'
CFG = HOME / '.config/gigatool/opencode.json'
PREV = HOME / 'hermes-pg-stage34fb-register-pilot/previous-opencode.json'
STAGE34FB_RECEIPT = HOME / 'hermes-pg-stage34fb-register-pilot/handoff-result.json'
PILOT_JS = HOME / 'hermes-pg-stage34eb-pilot-candidate/mt-pg-inbox-managed-stage34eb-pilot-unregistered.mjs'
CANARY_JS = HOME / 'hermes-pg-stage33i-ackfix/mt-pg-inbox-agent-stage33i.mjs'
GATE = HOME / '.config/bridge-pg-v2/managed-writes-stage34d-armed'
GATE_BYTES = b'ALLOW_MANAGED_WRITES_AFTER_EXCLUSIVE_SDK_REVIEW\n'
PILOT_STATE = HOME / '.local/state/mt-pg-inbox-managed-stage34e/state.json'
CANARY_STATE = HOME / '.local/state/mt-pg-inbox-agent-stage33f/state.json'
DISABLE_ATTEMPT = ROOT / '.policy-disable-attempt.json'
DISABLE_RECEIPT = ROOT / 'policy-disabled.json'
GATE_REVOKED = ROOT / 'write-gate-revoked.json'
BACKUP8 = ROOT / 'backup-eight-plugins.json'
DRAFT7 = ROOT / 'proposed-seven-plugins.json'
HANDOFF_RECEIPT = ROOT / 'handoff-result.json'
UNIT = HOME / '.config/systemd/user/hermes-pg-stage34g-retire.service'

PILOT = 'task_bddaadef89b7458fb7f8cf437dc0f023'
CANARY = 'task_f2c64cfdf7894537b35a19a6def977c6'
TASK_MID = 'pgv2-mg-34fc-pilot-task-20261123-v1'
REPORT_MID = 'pgv2-mg-' + PILOT[5:] + '-1-report'
REPORT_BODY = 'PGV2_MANAGED_PILOT_ROUNDTRIP_OK_20261123'
SHA8 = '4b32ac0fe83c902f5a7e2e5a749b104506987e1380aadc863ff15ca57b7786c3'
SHA7 = 'e5320f6e971073d5d2c43a64ce85d165cb49dc9a94e6cf7806009ebc2b59ded1'
PILOT_JS_SHA = 'b3679086583d77c96b6ed1c3dc7b1ed1656707dabba4d3ac74843fe64177d730'
CANARY_JS_SHA = '220da977562f9c0763d6b5d5640f686f54f041d357ed397c8482c0d4363f525c'
WAIT_SECONDS = 900
STABLE_SECONDS = 12
SERVICES = ('bridge-pg-v2-ssh-shadow.service',
            'hermes-pg-v2-stage33fb-cg.service',
            'hermes-pg-tunnel-stage33fd.service',
            'bridge-pg-v2-taskboard.service')

class Stop(Exception): pass

def need(yes, code):
    if not yes: raise Stop(code)

def digest(raw: bytes): return hashlib.sha256(raw).hexdigest()

def regular_owned(path: Path, private=True):
    try:
        s = path.lstat()
        return (stat.S_ISREG(s.st_mode) and s.st_uid == os.geteuid()
                and s.st_nlink == 1 and not path.is_symlink()
                and (not private or (s.st_mode & 0o077) == 0))
    except OSError: return False

def checked_bytes(path: Path, private=True) -> bytes:
    need(regular_owned(path,private), 'UNTRUSTED_FILE_' + path.name)
    return path.read_bytes()

def exact_file(path: Path, expected_sha: str, private=True) -> bytes:
    raw = checked_bytes(path,private)
    need(digest(raw) == expected_sha, 'CHANGED_SHA_' + path.name)
    return raw

def load_json(path: Path, private=True): return json.loads(checked_bytes(path,private))

def own_context():
    need(os.getuid()==os.geteuid()!=0 and pwd.getpwuid(os.geteuid()).pw_name=='kali'
         and Path.home()==HOME and Path.cwd().resolve()==ROOT,
         'WRONG_USER_OR_DIRECTORY')
    for p,expect in ((PILOT_JS,PILOT_JS_SHA),(CANARY_JS,CANARY_JS_SHA)):
        exact_file(p, expect,private=False)
    need(PILOT!=CANARY,'PILOT_OVERLAPS_CANARY')
    need(not ROOT.is_symlink() and ROOT.is_dir() and ROOT.stat().st_uid==os.geteuid(),
         'ROOT_UNTRUSTED')
    existing=load_json(STAGE34FB_RECEIPT)
    need(existing == {'outcome':'committed','code':'ONE_DISABLED_MANAGED_PILOT_REGISTERED',
                      'config_sha256':SHA8},'PREVIOUS_HANDOFF_RECEIPT_CHANGED')

def path_of(e):
    if isinstance(e,str): return e
    if isinstance(e,list) and len(e)==2 and isinstance(e[0],str) and isinstance(e[1],dict):
        return e[0]
    raise Stop('INVALID_PLUGIN_ENTRY')

def validate_config(raw: bytes, expected: int):
    o = json.loads(raw)
    ps=o.get('plugin'); need(isinstance(ps,list) and len(ps)==expected,'WRONG_PLUGIN_COUNT')
    ids=[path_of(p) for p in ps]
    need(ids.count(str(CANARY_JS))==1,'CANARY_PLUGIN_MISSING_OR_DUPLICATE')
    can=ps[ids.index(str(CANARY_JS))]
    need(isinstance(can,list) and can[1].get('dry_run') is False and
         can[1].get('task_ids')==[CANARY],'CANARY_PLUGIN_CONFIG_CHANGED')
    need(not any('mt-git-inbox' in x or 'mt-pg-inbox-canary' in x for x in ids),
         'GIT_OR_OLD_CANARY_PLUGIN_REINTRODUCED')
    if expected==8:
        need(ids.count(str(PILOT_JS))==1,'PILOT_PLUGIN_MISSING_OR_DUPLICATE')
        pilot=ps[ids.index(str(PILOT_JS))]
        need(isinstance(pilot,list) and pilot[1].get('dry_run') is False and
             pilot[1].get('tick_ms')==30000 and pilot[1].get('task_ids')==[PILOT],
             'PILOT_PLUGIN_CONFIG_CHANGED')
    else:
        need(str(PILOT_JS) not in ids,'PILOT_PLUGIN_STILL_REGISTERED')
    return o

def exact_configs():
    raw8=exact_file(CFG,SHA8)
    raw7=exact_file(PREV,SHA7)
    c8=validate_config(raw8,8); c7=validate_config(raw7,7)
    eight_except_pilot=[p for p in c8['plugin'] if path_of(p)!=str(PILOT_JS)]
    need(eight_except_pilot==c7['plugin'],'SEVEN_PLUGIN_CONTENT_DIFFERS')
    outside8=dict(c8); outside7=dict(c7)
    del outside8['plugin']; del outside7['plugin']
    need(outside8==outside7,'NONPLUGIN_CONFIG_DIFFERS')
    return raw8,raw7

def live_services():
    for unit in SERVICES:
        result=subprocess.run(['systemctl','--user','is-active','--quiet',unit],
                              capture_output=True,timeout=8)
        need(result.returncode==0,'DEPENDENCY_NOT_ACTIVE_'+unit.split('.')[0])

def actors():
    sys.path.insert(0,str(HOME/'hermes-pg-stage33e-shadow-clients'))
    from actor_import33e import actor_classes
    ChatGPT,Agent=actor_classes()
    return ChatGPT(),Agent()

def read_pg():
    cg,ag=actors()
    with cg._connect() as conn:
        with conn.cursor() as cur:
            cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            cur.execute('SELECT session_user AS actor')
            need(cur.fetchone()['actor']=='bridge_chatgpt','CHATGPT_LOGIN_MISMATCH')
            cur.execute('SELECT bridge.cg_list_managed_dialogs(%s,%s) AS r',(0,50))
            rows=cur.fetchone()['r']
            need(isinstance(rows,list) and len(rows)==1 and rows[0].get('task_id')==PILOT,
                 'MANAGED_REGISTRY_NOT_EXACTLY_PILOT')
            cur.execute('SELECT bridge.cg_managed_detail(%s) AS r',(PILOT,))
            detail=cur.fetchone()['r']
            cur.execute('SELECT bridge.cg_view_managed_dialog(%s,%s,%s) AS r',(PILOT,0,3))
            view=cur.fetchone()['r']
            deliveries=[]
            for seq in (1,2):
                cur.execute('SELECT bridge.delivery_status(%s,%s) AS r',(PILOT,seq))
                deliveries.append(cur.fetchone()['r'])
            cur.execute('SELECT bridge.dialog_metadata(%s) AS r',(CANARY,))
            can=cur.fetchone()['r']
            need(isinstance(can,dict) and can.get('last_seq')==6 and can.get('state')=='active',
                 'CANARY_DIALOG_CHANGED')
            for seq in (5,6):
                cur.execute('SELECT bridge.delivery_status(%s,%s) AS r',(CANARY,seq))
                need(cur.fetchone()['r'].get('status')=='delivered','CANARY_DELIVERY_CHANGED')
            cur.execute("SELECT count(*) AS n FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace s ON s.oid=p.pronamespace WHERE s.nspname='bridge'")
            need(cur.fetchone()['n']==42,'FUNCTION_COUNT_CHANGED')
            cur.execute("SELECT count(*) AS n FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace s ON s.oid=c.relnamespace WHERE s.nspname='bridge' AND c.relkind IN ('r','p')")
            need(cur.fetchone()['n']==4,'TABLE_COUNT_CHANGED')
    with ag._connect() as conn:
        with conn.cursor() as cur:
            cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            cur.execute('SELECT session_user AS actor')
            need(cur.fetchone()['actor']=='bridge_agent','AGENT_LOGIN_MISMATCH')
            cur.execute('SELECT bridge.ag_dispatch_list(%s,%s) AS r',(0,50))
            queued=cur.fetchone()['r']
    need(isinstance(detail,dict) and detail.get('registered') is True and
         detail.get('state')=='active' and detail.get('last_seq')==2 and
         detail.get('version')==2 and detail.get('next_actor')=='chatgpt',
         'PILOT_DIALOG_CHANGED')
    need(isinstance(view,dict) and view.get('task_id')==PILOT and
         isinstance(view.get('messages'),list) and len(view['messages'])==2,
         'PILOT_HISTORY_CHANGED')
    m1,m2=sorted(view['messages'],key=lambda x:x.get('seq',-1))
    need(m1.get('seq')==1 and m1.get('message_id')==TASK_MID and
         m1.get('sender')=='chatgpt' and m1.get('recipient')=='agent' and
         m1.get('kind')=='task' and m1.get('returned') is not True,
         'PILOT_TASK_IDENTITY_CHANGED')
    need(m2.get('seq')==2 and m2.get('message_id')==REPORT_MID and
         m2.get('sender')=='agent' and m2.get('recipient')=='chatgpt' and
         m2.get('kind')=='report' and m2.get('body')==REPORT_BODY and
         m2.get('returned') is not True,'PILOT_REPORT_IDENTITY_CHANGED')
    need(all(isinstance(x,dict) and x.get('status')=='delivered' for x in deliveries),
         'BOTH_PILOT_RECEIPTS_REQUIRED')
    need(queued==[],'MANAGED_DISPATCH_LIST_NOT_EMPTY')
    return detail

def pilot_state_safe():
    s=load_json(PILOT_STATE)
    need(isinstance(s,dict) and s.get('inflight',{})=={} and
         isinstance(s.get('seen'),dict) and s['seen'].get(PILOT,0)>=1,
         'PILOT_INFLIGHT_OR_SEEN_INCONSISTENT')
    c=load_json(CANARY_STATE)
    need(isinstance(c,dict) and c.get('inflight')=={} and
         c.get('seen',{}).get(CANARY)==5,'CANARY_DURABLE_STATE_CHANGED')
    return digest(checked_bytes(PILOT_STATE)),digest(checked_bytes(CANARY_STATE))

def gate_valid():
    raw=checked_bytes(GATE)
    need(raw==GATE_BYTES and stat.S_IMODE(GATE.stat().st_mode)==0o600,
         'GATE_CONTENT_OR_MODE_CHANGED')

def gate_absent():
    need(not GATE.exists() and not GATE.is_symlink(),'MANAGED_WRITE_GATE_STILL_PRESENT')

def journal(path: Path, obj):
    need(not path.exists() and not path.is_symlink(),'JOURNAL_ALREADY_EXISTS_'+path.name)
    raw=(json.dumps(obj,sort_keys=True,separators=(',',':'))+'\n').encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f: f.write(raw); f.flush(); os.fsync(f.fileno())
    sync_dir(path.parent)

def sync_dir(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)

def atomic_write(path: Path, raw: bytes, replace=False):
    tmp=path.with_name('.'+path.name+'.tmp-'+str(os.getpid()))
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb') as f: f.write(raw); f.flush(); os.fsync(f.fileno())
        if replace:
            need(path.exists() and not path.is_symlink() and regular_owned(path),
                 'REPLACE_DEST_UNTRUSTED')
            os.replace(tmp,path)
        else:
            need(not path.exists() and not path.is_symlink(),'FILE_ALREADY_EXISTS')
            os.link(tmp,path);os.unlink(tmp)
        sync_dir(path.parent)
    finally: tmp.unlink(missing_ok=True)

def pre_disable():
    own_context();live_services();exact_configs();gate_valid();pilot_state_safe()
    d=read_pg()
    need(d.get('dispatch_enabled') is True and d.get('policy_version')==1,
         'PILOT_NOT_ENABLED_POLICY_1')
    need(not DISABLE_ATTEMPT.exists() and not DISABLE_ATTEMPT.is_symlink(),
         'POLICY_DISABLE_ALREADY_ATTEMPTED')
    print('STAGE34G_8_PLUGIN_CONFIG_PILOT_AND_CANARY_VERIFIED')
    print('STAGE34G_BOTH_PILOT_RECEIPTS_DELIVERED_INFLIGHT_EMPTY')
    print('STAGE34G_PILOT_DISPATCH_ON_POLICY_VERSION_1')
    print('STAGE34G_PREFLIGHT_NO_MUTATIONS')

def disable_once():
    pre_disable()
    journal(DISABLE_ATTEMPT,{'pilot_task_id':PILOT,'policy_from':1,'policy_to':2,
                            'expected_dialog_version':2,'no_retry_if_commit_unknown':True})
    print('STAGE34G_DISABLE_ATTEMPT_JOURNALED_NO_RETRY',flush=True)
    cg,_=actors()
    with cg._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout='15000ms'")
            cur.execute('SELECT session_user AS actor')
            need(cur.fetchone()['actor']=='bridge_chatgpt','WRITE_ROLE_NOT_CHATGPT')
            cur.execute('SELECT bridge.cg_set_managed_dispatch(%s,%s,%s,%s) AS r',
                        (PILOT,False,1,2))
            result=cur.fetchone()['r']
            need(isinstance(result,dict) and result.get('task_id')==PILOT and
                 result.get('dispatch_enabled') is False and
                 result.get('changed') is True and
                 result.get('policy_version')==2,'DISABLE_RESULT_UNEXPECTED')
            cur.execute('SELECT bridge.cg_managed_detail(%s) AS r',(PILOT,))
            changed=cur.fetchone()['r']
            need(changed and changed.get('dispatch_enabled') is False and
                 changed.get('policy_version')==2 and changed.get('last_seq')==2 and
                 changed.get('version')==2,'DISABLE_TX_POSTCONDITION_FAILED')
    print('STAGE34G_PILOT_DISPATCH_DISABLED_DB_COMMITTED',flush=True)
    journal(DISABLE_RECEIPT,{'pilot_task_id':PILOT,'policy_version':2,'committed':True})
    revoke_gate_after_verified_db()
    print('STAGE34G_QUIESCE_PHASE_A_COMPLETE')

def confirmed_disabled():
    own_context();live_services();pilot_state_safe()
    d=read_pg()
    need(d.get('dispatch_enabled') is False and d.get('policy_version')==2,
         'PILOT_NOT_DISABLED_POLICY_2')
    return d

def revoke_gate_after_verified_db():
    # Recovery only after a real role-bound read verifies committed SQL policy.
    need(regular_owned(DISABLE_ATTEMPT),'DISABLE_ATTEMPT_RECEIPT_MISSING')
    confirmed_disabled()
    if GATE.exists() or GATE.is_symlink():
        gate_valid()
        GATE.unlink()
        sync_dir(GATE.parent)
        print('STAGE34G_MANAGED_WRITE_GATE_EXACT_FILE_REMOVED',flush=True)
    gate_absent()
    if not GATE_REVOKED.exists():
        journal(GATE_REVOKED,{'pilot_task_id':PILOT,'file_removed':str(GATE),
                              'policy_version':2})
    else:
        need(load_json(GATE_REVOKED).get('pilot_task_id')==PILOT,
             'GATE_REVOKE_RECEIPT_INVALID')
    print('STAGE34G_MANAGED_WRITE_GATE_REVOKED')

def verify_quiesced(require_cfg8=True):
    confirmed_disabled();gate_absent()
    need(regular_owned(DISABLE_ATTEMPT),'DISABLE_JOURNAL_MISSING')
    need(regular_owned(GATE_REVOKED),'GATE_REVOKE_RECEIPT_MISSING')
    if require_cfg8: exact_configs()
    print('STAGE34G_POLICY_2_DISPATCH_OFF_BOTH_DELIVERED')
    print('STAGE34G_WRITE_GATE_ABSENT_AND_NO_INFLIGHT')
    print('STAGE34G_PILOT_HISTORY_AND_CANARY_UNCHANGED')

def prepare_retirement():
    own_context();live_services();verify_quiesced(require_cfg8=True)
    need(not any(p.exists() or p.is_symlink() for p in (BACKUP8,DRAFT7,HANDOFF_RECEIPT,UNIT)),
         'RETIREMENT_ALREADY_PREPARED')
    raw8,raw7=exact_configs()
    atomic_write(BACKUP8,raw8)
    atomic_write(DRAFT7,raw7)
    print('STAGE34G_SEVEN_PLUGIN_BYTE_EXACT_DRAFT_PREPARED')
    # Installation of a static user service is INERT until explicitly started.
    need(UNIT.parent.is_dir() and not UNIT.parent.is_symlink() and
         UNIT.parent.stat().st_uid==os.geteuid(),'SYSTEMD_UNIT_DIR_UNTRUSTED')
    service=(
        '[Unit]\nDescription=Hermes PG Stage 34G retire completed pilot after MultiTool exit\n'
        'Wants=bridge-pg-v2-ssh-shadow.service\nAfter=bridge-pg-v2-ssh-shadow.service\n'
        '[Service]\nType=oneshot\nWorkingDirectory=/home/kali/hermes-pg-stage34g-quiesce-and-retire\n'
        'ExecStart=/home/kali/bridge-pg-v2/.venv/bin/python '
        '/home/kali/hermes-pg-stage34g-quiesce-and-retire/stage34g.py worker\n'
        'TimeoutStartSec=1100\nRemainAfterExit=yes\nRestart=no\nNoNewPrivileges=yes\n'
        'Environment=PYTHONNOUSERSITE=1\nUnsetEnvironment=APPDIR APPIMAGE\nUMask=0077\n')
    atomic_write(UNIT,service.encode())
    subprocess.run(['systemctl','--user','daemon-reload'],check=True,timeout=15)
    r=subprocess.run(['systemctl','--user','is-active','--quiet','hermes-pg-stage34g-retire.service'],
                     capture_output=True,timeout=8)
    need(r.returncode!=0,'NEW_RETIRE_SERVICE_ALREADY_ACTIVE')
    print('STAGE34G_RETIREMENT_SERVICE_INSTALLED_INACTIVE')
    print('STAGE34G_EIGHT_PLUGIN_CONFIG_STILL_ACTIVE')

def prepared():
    raw8,raw7=exact_configs()
    need(checked_bytes(BACKUP8)==raw8 and checked_bytes(DRAFT7)==raw7,
         'PREPARED_CONFIG_BYTES_CHANGED')
    need(regular_owned(UNIT),'STAGE34G_SERVICE_NOT_PREPARED')
    return raw8,raw7

def safe_processes():
    found=[];unknown=[];uid=os.geteuid()
    for p in Path('/proc').iterdir():
        if not p.name.isdecimal():continue
        try:
            if p.stat().st_uid != uid:continue
            comm=(p/'comm').read_text('utf8').strip()[:32]
            low=comm.lower()
            mt=any(s in low for s in ('multitool','gigatool','opencode'))
            if not mt and low in ('node','bun','electron'):
                try:
                    raw=(p/'cmdline').read_bytes().lower()
                    mt=any(s in raw for s in (b'multitool',b'gigatool',b'opencode'))
                except OSError:
                    unknown.append(int(p.name));continue
            if mt:
                st=(p/'stat').read_text('utf8');parts=st[st.rfind(')')+2:].split()
                found.append({'pid':int(p.name),'comm':comm,'start_ticks':int(parts[19])})
        except FileNotFoundError:continue
        except (OSError,ValueError,IndexError):unknown.append(int(p.name))
    return sorted(found,key=lambda x:x['pid']),sorted(set(unknown))

def arm():
    own_context();live_services();verify_quiesced()
    prepared()
    need(not HANDOFF_RECEIPT.exists(),'HANDOFF_ALREADY_ATTEMPTED')
    r=subprocess.run(['systemctl','--user','is-enabled','hermes-pg-stage34g-retire.service'],
                     capture_output=True,text=True,timeout=8)
    need(r.stdout.strip()!='enabled','RETIREMENT_SERVICE_MUST_REMAIN_STATIC')
    subprocess.run(['systemctl','--user','start','--no-block','hermes-pg-stage34g-retire.service'],
                   check=True,timeout=12)
    print('STAGE34G_RETIREMENT_ARMED_CLOSE_ALL_MULTITOOL_WINDOWS')
    print('STAGE34G_DO_NOT_REOPEN_UNTIL_POST_HANDOFF_VERIFIED')

def worker():
    own_context();live_services();verify_quiesced()
    prepared()
    need(not HANDOFF_RECEIPT.exists(),'HANDOFF_ALREADY_ATTEMPTED')
    print('STAGE34G_WAIT_FOR_ALL_MULTITOOL_PROCESSES_TO_EXIT',flush=True)
    started=time.monotonic();quiet=None
    while time.monotonic()-started<WAIT_SECONDS:
        found,unknown=safe_processes()
        if found or unknown:quiet=None
        elif quiet is None:quiet=time.monotonic()
        elif time.monotonic()-quiet>=STABLE_SECONDS:break
        time.sleep(2)
    else:
        journal(HANDOFF_RECEIPT,{'outcome':'blocked','code':'MULTITOOL_NOT_QUIET'})
        raise Stop('MULTITOOL_NOT_QUIET')
    found,unknown=safe_processes()
    need(not found and not unknown,'MULTITOOL_REAPPEARED')
    live_services();verify_quiesced()
    old_pilot_state_hash,old_canary_state_hash=pilot_state_safe()
    raw8,raw7=prepared()
    found,unknown=safe_processes()
    need(not found and not unknown,'MULTITOOL_REAPPEARED_AT_COMMIT')
    need(pilot_state_safe()==(old_pilot_state_hash,old_canary_state_hash),
         'STATE_CHANGED_DURING_SWITCH')
    atomic_write(CFG,raw7,replace=True)
    os.chmod(CFG,0o600)
    need(digest(checked_bytes(CFG))==SHA7,'SEVEN_PLUGIN_COMMIT_HASH_MISMATCH')
    journal(HANDOFF_RECEIPT,{'outcome':'committed','code':'COMPLETED_MANAGED_PILOT_RETIRED',
                             'config_sha256':SHA7})
    print('STAGE34G_ATOMIC_CONFIG_EIGHT_TO_SEVEN_COMMITTED',flush=True)
    print('STAGE34G_PILOT_PLUGIN_UNREGISTERED_HISTORY_RETAINED',flush=True)

def verify_after():
    own_context();live_services();confirmed_disabled();gate_absent()
    raw=exact_file(CFG,SHA7)
    validate_config(raw,7)
    need(raw==checked_bytes(DRAFT7) and raw==checked_bytes(PREV),'POST_HANDOFF_CONFIG_NOT_PINNED')
    receipt=load_json(HANDOFF_RECEIPT)
    need(receipt=={'outcome':'committed','code':'COMPLETED_MANAGED_PILOT_RETIRED',
                   'config_sha256':SHA7},'HANDOFF_RECEIPT_NOT_COMMITTED')
    pilot_state_safe()
    print('STAGE34G_SEVEN_PLUGINS_RESTORED_CANARY_UNCHANGED')
    print('STAGE34G_MANAGED_DISPATCH_OFF_WRITE_GATE_ABSENT')
    print('STAGE34G_BOTH_PILOT_DELIVERIES_STILL_DELIVERED')
    print('STAGE34G_PILOT_HISTORY_RETAINED_2_MESSAGES')
    print('STAGE34G_FINAL_POST_HANDOFF_VERIFIED')

def inspect_policy():
    """Read-only diagnosis for an unknown PostgreSQL COMMIT outcome."""
    own_context();live_services()
    d=read_pg()
    state='off' if d.get('dispatch_enabled') is False else 'on'
    print('STAGE34G_READONLY_POLICY_'+state.upper())
    print(json.dumps({'pilot_task_id':PILOT,'dispatch_enabled':d.get('dispatch_enabled'),
                      'policy_version':d.get('policy_version'),
                      'dialog_version':d.get('version'),'last_seq':d.get('last_seq'),
                      'deliveries':'both_delivered',
                      'write_gate_present':GATE.exists() or GATE.is_symlink()},sort_keys=True))

def status():
    own_context()
    raw=checked_bytes(CFG)
    procs,unknown=safe_processes()
    print(json.dumps({'config_sha256':digest(raw),'eight_plugins':digest(raw)==SHA8,
        'seven_plugins':digest(raw)==SHA7,'write_gate_exists':GATE.exists() or GATE.is_symlink(),
        'disable_journal_exists':DISABLE_ATTEMPT.exists(),'gate_revoked_receipt':GATE_REVOKED.exists(),
        'retirement_prepared':DRAFT7.exists(),'retirement_receipt':
          load_json(HANDOFF_RECEIPT) if regular_owned(HANDOFF_RECEIPT) else None,
        'multitool_processes':procs,'unknown_pids':unknown},indent=2,sort_keys=True))

def main(argv):
    try:
        need(len(argv)==2 and argv[1] in (
            'pre-disable','disable-once','revoke-after-confirmed-db','verify-quiesced',
            'prepare-retirement','arm','worker','verify-after','status','inspect-policy'),
            'UNKNOWN_MODE')
        {'pre-disable':pre_disable,'disable-once':disable_once,
         'revoke-after-confirmed-db':revoke_gate_after_verified_db,
         'verify-quiesced':verify_quiesced,'prepare-retirement':prepare_retirement,
         'arm':arm,'worker':worker,'verify-after':verify_after,
         'status':status,'inspect-policy':inspect_policy}[argv[1]]()
        return 0
    except Exception as exc:
        code=str(exc) if isinstance(exc,Stop) else 'UNEXPECTED_'+type(exc).__name__
        print('STAGE34G_STOP_'+code[:110],file=sys.stderr,flush=True)
        return 2

if __name__=='__main__':raise SystemExit(main(sys.argv))
