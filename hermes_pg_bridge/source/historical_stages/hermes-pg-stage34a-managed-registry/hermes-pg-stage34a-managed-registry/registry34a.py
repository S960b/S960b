#!/usr/bin/env python3
"""Stage 34A real-actor, read-only inventory and ROLLBACK-ONLY SQL proof.

No service/config changes; no messages or queue writes persist. The test
uses live PostgreSQL actor identities, not SET ROLE impersonation.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import stat
import sys
import uuid
from pathlib import Path

ROOT = Path('/home/kali/hermes-pg-stage34a-managed-registry')
CFG = Path('/home/kali/.config/gigatool/opencode.json')
PLUGIN = Path('/home/kali/hermes-pg-stage33i-ackfix/mt-pg-inbox-agent-stage33i.mjs')
CFG_SHA = 'e5320f6e971073d5d2c43a64ce85d165cb49dc9a94e6cf7806009ebc2b59ded1'
PLUGIN_SHA = '220da977562f9c0763d6b5d5640f686f54f041d357ed397c8482c0d4363f525c'
CANARY = 'task_f2c64cfdf7894537b35a19a6def977c6'
IMPORT = '/home/kali/hermes-pg-stage33e-shadow-clients'

class Halt(Exception):
    pass

def need(ok, code):
    if not ok:
        raise Halt(code)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def safe_regular(path):
    st = path.lstat()
    return (stat.S_ISREG(st.st_mode) and not path.is_symlink()
            and st.st_nlink == 1 and st.st_uid == os.geteuid())

def plugin_entries(obj):
    entries = obj.get('plugin')
    need(isinstance(entries, list) and len(entries) == 7, 'CONFIG_PLUGIN_COUNT_CHANGED')
    names=[]
    for e in entries:
        if isinstance(e, str):
            names.append(e)
        elif isinstance(e,list) and len(e)==2 and isinstance(e[0],str) and isinstance(e[1],dict):
            names.append(e[0])
        else:
            raise Halt('CONFIG_ENTRY_INVALID')
    need(names.count(str(PLUGIN))==1,'ACTIVE_PLUGIN_MISMATCH')
    need(all('mt-git-inbox' not in n and 'stage33f.mjs' not in n for n in names),
         'OLD_GIT_OR_CANARY_PLUGIN_PRESENT')
    instance = next(e for e in entries if isinstance(e,list) and e[0]==str(PLUGIN))
    need(instance[1].get('dry_run') is False and instance[1].get('task_ids') == [CANARY],
         'ACTIVE_CANARY_FILTER_CHANGED')

def pin_runtime():
    need(os.geteuid()!=0 and os.geteuid()==os.getuid() and str(Path.home())=='/home/kali',
         'WRONG_EXECUTION_USER')
    for path,expected in ((CFG,CFG_SHA),(PLUGIN,PLUGIN_SHA)):
        need(safe_regular(path) and sha(path)==expected,'LIVE_PIN_MISMATCH')
    plugin_entries(json.loads(CFG.read_text()))

def actor_classes():
    sys.path.insert(0,IMPORT)
    from actor_import33e import actor_classes as get
    return get()

def verify_canary(ChatGPT,Agent):
    chat=ChatGPT();agent=Agent()
    h=chat.health()
    need(isinstance(h,dict) and h.get('ok') is True,'DB_HEALTH_UNAVAILABLE')
    meta=chat.dialog_metadata(CANARY)
    need(meta and meta.get('state')=='active' and meta.get('last_seq')==6,
         'CANARY_SEQUENCE_CHANGED')
    for seq in (5,6):
        d=chat.delivery_status(CANARY,seq)
        need(isinstance(d,dict) and d.get('status')=='delivered','CANARY_RECEIPT_NOT_FINAL')
    need(agent.dialog_metadata(CANARY).get('last_seq')==6,'ACTOR_VIEWS_DISAGREE')

def proof_permissions(cg,ag):
    with cg._connect() as c:
        with c.cursor() as cur:
            cur.execute("SELECT session_user AS actor")
            need(cur.fetchone()['actor']=='bridge_chatgpt','CHATGPT_IDENTITY_WRONG')
            cur.execute("SELECT bridge.cg_managed_status(%s) AS result",(CANARY,))
            r=cur.fetchone()['result']
            need(r.get('registered') is False,'LEGACY_CANARY_ENROLLED')
            cur.execute("SELECT pg_catalog.has_table_privilege(session_user,'bridge.managed_dialogs','SELECT') AS allowed")
            need(cur.fetchone()['allowed'] is False,'CHATGPT_TABLE_ACCESS_LEAK')
            cur.execute("SELECT pg_catalog.has_function_privilege(session_user,'bridge.ag_dispatch_list(integer,integer)','EXECUTE') AS allowed")
            need(cur.fetchone()['allowed'] is False,'CHATGPT_AGENT_FUNCTION_ACCESS')
        c.rollback()
    with ag._connect() as c:
        with c.cursor() as cur:
            cur.execute("SELECT session_user AS actor")
            need(cur.fetchone()['actor']=='bridge_agent','AGENT_IDENTITY_WRONG')
            cur.execute("SELECT bridge.ag_dispatch_list(0,20) AS result")
            need(cur.fetchone()['result']==[],'DISPATCH_LIST_NOT_EMPTY')
            cur.execute("SELECT pg_catalog.has_table_privilege(session_user,'bridge.managed_dialogs','SELECT') AS allowed")
            need(cur.fetchone()['allowed'] is False,'AGENT_TABLE_ACCESS_LEAK')
            cur.execute("SELECT pg_catalog.has_function_privilege(session_user,'bridge.cg_create_managed_dialog(text,text)','EXECUTE') AS allowed")
            need(cur.fetchone()['allowed'] is False,'AGENT_CHATGPT_FUNCTION_ACCESS')
        c.rollback()

def rollback_only_live_positive(cg):
    """Execute a real create and idempotent retry in one UNCOMMITTED transaction."""
    request='stage34a_'+uuid.uuid4().hex
    title='Stage34A rollback-only shadow proof; do not dispatch'
    with cg._connect() as c:
        with c.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout='10000ms'")
            cur.execute('SELECT bridge.cg_create_managed_dialog(%s,%s) AS result',(request,title))
            a=cur.fetchone()['result']
            need(a.get('created') is True and a.get('managed') is True and
                 a.get('dispatch_enabled') is False and re.fullmatch(r'task_[0-9a-f]{32}',a.get('task_id','')),
                 'ROLLBACK_CREATE_CONTRACT_BROKEN')
            tid=a['task_id']
            cur.execute('SELECT bridge.cg_managed_status(%s) AS result',(tid,))
            state=cur.fetchone()['result']
            need(state.get('registered') is True and state.get('dispatch_enabled') is False
                 and state.get('last_seq')==0 and state.get('next_actor')=='chatgpt',
                 'ROLLBACK_MANAGED_STATUS_BROKEN')
            cur.execute('SELECT bridge.cg_create_managed_dialog(%s,%s) AS result',(request,title))
            b=cur.fetchone()['result']
            need(b.get('task_id')==tid and b.get('created') is False and b.get('dispatch_enabled') is False,
                 'ROLLBACK_IDEMPOTENCY_BROKEN')
        c.rollback()
    need(cg.creation_status(request) is None, 'ROLLBACK_CREATION_PERSISTED')
    return True

def readonly_only():
    pin_runtime()
    ChatGPT,Agent=actor_classes()
    verify_canary(ChatGPT,Agent)
    proof_permissions(ChatGPT(),Agent())
    print('STAGE34A_ACTOR_BOUND_REGISTRY_READ_ONLY_VERIFIED')
    print('STAGE34A_UNMANAGED_LEGACY_DIALOGS_EXCLUDED')
    print('STAGE34A_NO_MANAGED_DISPATCH_ENABLED')
    print('STAGE34A_SEVEN_PLUGINS_CANARY_FILTER_UNCHANGED')

def preflight():
    pin_runtime()
    ChatGPT,Agent=actor_classes()
    verify_canary(ChatGPT,Agent)
    with ChatGPT()._connect() as cn:
        with cn.cursor() as c:
            c.execute("SELECT count(*) AS n FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='bridge'")
            need(c.fetchone()['n']==25,'PREEXISTING_FUNCTION_COUNT_CHANGED')
            c.execute("SELECT count(*) AS n FROM pg_catalog.pg_class cl JOIN pg_catalog.pg_namespace n ON n.oid=cl.relnamespace WHERE n.nspname='bridge' AND cl.relname='managed_dialogs'")
            need(c.fetchone()['n']==0,'MANAGED_TABLE_ALREADY_PRESENT')
        cn.rollback()
    print('STAGE34A_READ_ONLY_PRE_MIGRATION_PREFLIGHT_OK')

def main():
    mode=sys.argv[1] if len(sys.argv)==2 else ''
    try:
        if mode=='preflight': preflight()
        elif mode=='verify': readonly_only()
        elif mode=='proof':
            pin_runtime()
            ChatGPT,Agent=actor_classes()
            verify_canary(ChatGPT,Agent)
            proof_permissions(ChatGPT(),Agent())
            rollback_only_live_positive(ChatGPT())
            print('STAGE34A_REAL_CHATGPT_CREATE_IDEMPOTENCY_ROLLBACK_OK')
            print('STAGE34A_REAL_ROLE_GUARDS_AND_DISPATCH_DENIAL_OK')
            print('STAGE34A_NO_DIALOG_OR_MESSAGE_COMMITTED_BY_TEST')
        else: raise Halt('INVALID_MODE')
    except Exception as e:
        code=str(e) if isinstance(e,Halt) else 'UNEXPECTED_ERROR_'+type(e).__name__
        print('STAGE34A_STOP_'+code[:85],file=sys.stderr)
        raise SystemExit(2)

if __name__=='__main__':main()
