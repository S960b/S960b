#!/usr/bin/env python3
"""Stage 33F-A read-only deployment inventory. Never run an SDK prompt here."""
from __future__ import annotations
import hashlib
import json
import os
import pwd
import stat
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit,unquote
from urllib.request import urlopen,ProxyHandler,build_opener

from stage33f_agent_cli import CANARY,REPORT_MID,TASK_MID

ROOT=Path('/home/kali/hermes-pg-stage33f-sdk-preflight')
STAGE33E=Path('/home/kali/hermes-pg-stage33e-shadow-clients')
STAGE33C=Path('/home/kali/hermes-pg-stage33c-shadow-tunnel')
CONFIG=Path('/home/kali/.config/gigatool/opencode.json')
BASE=Path('/home/kali/bridge-pg-v2/dist/mt-pg-inbox-canary.mjs')
NEW=ROOT/'mt-pg-inbox-agent-stage33f.mjs'

class Stop(Exception):pass

def need(ok,category):
    if not ok:raise Stop(category)

def safe_regular(p):
    try:
        st=p.lstat()
        return stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid() and st.st_nlink==1
    except OSError:return False

def path_of(e):
    if isinstance(e,str):return e,{}
    if isinstance(e,list) and len(e)==2 and isinstance(e[0],str) and isinstance(e[1],dict):
        return e[0],e[1]
    raise Stop('INVALID_PLUGIN_ENTRY')

def normalize(p):
    if p.startswith('file:///'):
        u=urlsplit(p)
        need(not (u.netloc or u.query or u.fragment),'UNEXPECTED_PLUGIN_URI')
        return unquote(u.path)
    return p

def state(unit):
    try:
        r=subprocess.run(['systemctl','--user','is-active',unit],capture_output=True,
                         text=True,timeout=5,check=False)
        return (r.stdout.strip() if r.returncode==0 else 'inactive')
    except Exception:return 'unknown'

def http_ping():
    # No proxy and no external requests.
    try:
        opener=build_opener(ProxyHandler({}))
        with opener.open('http://127.0.0.1:18772/ping',timeout=2) as r:
            return r.status==200
    except Exception:return False

def proc_summary():
    counts={}
    for p in Path('/proc').iterdir():
        if not p.name.isdecimal():continue
        try:name=(p/'comm').read_text(encoding='utf-8').strip()
        except (OSError,UnicodeError):continue
        key=name.lower()
        if any(s in key for s in ('multitool','gigatool','opencode','node')):
            counts[name]=counts.get(name,0)+1
    return counts

def check_config():
    need(safe_regular(CONFIG),'CONFIG_NOT_TRUSTED')
    raw=CONFIG.read_bytes()
    x=json.loads(raw)
    entries=x.get('plugin')
    need(isinstance(entries,list) and len(entries)==8,'PLUGIN_COUNT_CHANGED')
    pg=[]
    for item in entries:
        path,opts=path_of(item)
        basename=Path(normalize(path)).name
        if 'mt-pg-inbox' in basename:pg.append((normalize(path),opts))
    need(len(pg)==1 and pg[0][0]=='/home/kali/bridge-pg-v2/dist/mt-pg-inbox-canary.mjs'
         and pg[0][1].get('dry_run') is False and pg[0][1].get('task_ids')==[CANARY],
         'ACTIVE_PG_PLUGIN_NOT_EXACT_CANARY')
    need(all(str(NEW) not in str(item) for item in entries),'STAGE33F_CANDIDATE_ALREADY_ACTIVE')
    need(safe_regular(BASE) and safe_regular(NEW),'PLUGIN_SOURCE_NOT_TRUSTED')
    old=BASE.read_text('utf-8');new=NEW.read_text('utf-8')
    for marker in ('Object.freeze(["'+CANARY+'"]);','session.prompt','ctx.taskIds && !ctx.taskIds.includes(dialog.task_id)'):
        need(marker in new,'CANDIDATE_SOURCE_UNEXPECTED')
    need('const dryRun = false; // STAGE33F PROSPECTIVE ONLY: NOT REGISTERED' in new,
         'CANDIDATE_DRYRUN_MISMATCH')
    need('/home/kali/hermes-pg-stage33f-sdk-preflight/stage33f_agent_cli.py' in new
         and '/home/kali/hermes-pg-stage33e-shadow-clients/actor_agent_cli.py' not in new
         and 'bridge_pg.cli' not in new,'CANDIDATE_CLI_MISMATCH')
    need('const STATE_DIR = join(HOME, ".local/state/mt-pg-inbox-agent-stage33f");' in new,
         'CANDIDATE_STATE_NOT_ISOLATED')
    need('pgv2-stage33f-sdk-report-v1' in new and
         'PGV2_ROLE_BOUND_MT_SDK_ACK_20261122' in new,'CANDIDATE_PROMPT_UNEXPECTED')
    return {'config_sha256':hashlib.sha256(raw).hexdigest(),
            'original_pg_plugin_sha256':hashlib.sha256(BASE.read_bytes()).hexdigest(),
            'new_candidate_sha256':hashlib.sha256(NEW.read_bytes()).hexdigest(),
            'active_pg_plugin_count':len(pg), 'plugin_total':len(entries),
            'new_candidate_registered':False}

def verify_database():
    sys.path.insert(0,str(STAGE33E))
    from actor_import33e import actor_classes
    C,A=actor_classes();cg=C();ag=A()
    need(cg.health().get('ok') is True and ag.health().get('ok') is True,'ACTOR_DB_HEALTH_FAILED')
    meta=cg.dialog_metadata(CANARY)
    need(meta.get('state')=='active' and meta.get('last_seq')==4 and meta.get('next_actor')=='chatgpt',
         'CANARY_UNEXPECTED_TURN')
    need(ag.dialog_metadata(CANARY).get('last_seq')==4,'ACTOR_ROLE_VIEW_MISMATCH')
    need(cg.delivery_status(CANARY,3).get('status')=='delivered','OLD_CANARY_NOT_DELIVERED')
    need(cg.send_status(CANARY,REPORT_MID).get('found') is False,
         'STAGE33F_REPORT_ALREADY_EXISTS')
    need(cg.send_status(CANARY,TASK_MID).get('found') is False,
         'STAGE33F_TASK_ALREADY_EXISTS')
    return {'actor_real_login_roles':['bridge_chatgpt','bridge_agent'],
            'canary_last_seq':4,'canary_next_actor':'chatgpt',
            'stage33f_test_messages_absent':True}

def main():
    try:
        need(os.geteuid()!=0 and pwd.getpwuid(os.geteuid()).pw_name=='kali' and
             Path.home()==Path('/home/kali') and Path.cwd().resolve()==ROOT,
             'INVALID_OS_CONTEXT')
        need(state('bridge-pg-v2-ssh-shadow.service')=='active', 'SHADOW_FORWARD_OFFLINE')
        need(state('hermes-pg-v2-shadow-cg.service')=='active', 'SHADOW_CG_OFFLINE')
        need(state('hermes-pg-tunnel-one-shot.service')=='active', 'CURRENT_SECURE_TUNNEL_OFFLINE')
        need(http_ping(),'SHADOW_LOOPBACK_MCP_OFFLINE')
        p=check_config();d=verify_database()
        print('STAGE33F_REAL_DB_ACTOR_ROLES_AND_CANARY_UNCHANGED')
        print('STAGE33F_EXACTLY_ONE_LIVE_CANARY_DISPATCHER_CONFIRMED')
        print('STAGE33F_NEW_SDK_CANDIDATE_NOT_REGISTERED')
        print('STAGE33F_SDK_DURABLE_GUARDS_PRESENT_IN_CANDIDATE')
        info={'stage':'33F-A','database':d,'config':p,
              'services':{n:state(n) for n in
                ('bridge-pg-v2-ssh-shadow.service','hermes-pg-v2-shadow-cg.service',
                 'hermes-pg-tunnel-one-shot.service')},
              'sdk_runtime_process_names_only':proc_summary(),
              'sdk_actual_execution_attempted':False,'db_writes_performed':False,
              'candidate_activation_requires_separate_approval':True}
        print(json.dumps(info,ensure_ascii=False,sort_keys=True,indent=2))
        print('STAGE33F_AUDIT_AND_CANDIDATE_READY_NO_CUTOVER')
        return 0
    except Stop as e:
        print('STAGE33F_STOP_'+str(e),file=sys.stderr)
        return 2
    except Exception as e:
        print('STAGE33F_STOP_'+type(e).__name__,file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main())
