#!/usr/bin/env python3
"""Read-only real-role verification for 39->42 function Stage 34C migration."""
from __future__ import annotations
import hashlib
import json
import os
import sys
from pathlib import Path
from render34c import CONFIG,SRC,DST,CONFIG_SHA,CURRENT_SHA,CANARY,pin,render,sha,trusted
from managed_agent_cli34c import dispatch, parse, Refused

class Stop(Exception):pass

def need(x,c):
    if not x:raise Stop(c)

def get_actors():
    sys.path.insert(0,'/home/kali/hermes-pg-stage33e-shadow-clients')
    from actor_import33e import actor_classes
    return actor_classes()

def check_database(installed):
    C,A=get_actors();cg=C();ag=A()
    need(cg.health().get('ok') is True and ag.health().get('ok') is True,'ROLE_DB_HEALTH_FAILED')
    for actor in (cg,ag):
        meta=actor.dialog_metadata(CANARY)
        need(meta.get('last_seq')==6 and meta.get('state')=='active','CANARY_CHANGED')
        for seq in (5,6):
            need(actor.delivery_status(CANARY,seq).get('status')=='delivered','CANARY_RECEIPT_CHANGED')
    with cg._connect() as conn:
        with conn.cursor() as c:
            c.execute('SET TRANSACTION READ ONLY')
            c.execute('SELECT session_user AS actor')
            need(c.fetchone()['actor']=='bridge_chatgpt','CHATGPT_ROLE_WRONG')
            c.execute('SELECT bridge.cg_list_managed_dialogs(0,50) AS result')
            need(c.fetchone()['result']==[],'MANAGED_REGISTRY_NOT_EMPTY')
            c.execute("SELECT count(*) AS n FROM pg_proc p JOIN pg_namespace s ON s.oid=p.pronamespace WHERE s.nspname='bridge'")
            need(c.fetchone()['n']==(42 if installed else 39),'UNEXPECTED_FUNCTION_COUNT')
            c.execute("SELECT count(*) AS n FROM pg_class c JOIN pg_namespace s ON s.oid=c.relnamespace WHERE s.nspname='bridge' AND c.relkind IN ('r','p')")
            need(c.fetchone()['n']==4,'TABLE_COUNT_CHANGED')
            if installed:
                for signature in ('bridge.ag_view_managed_dialog(text,integer,integer)',
                                  'bridge.ag_managed_delivery_status(text,bigint)',
                                  'bridge.ag_managed_report_status(text,text)'):
                    c.execute('SELECT has_function_privilege(session_user,%s,\'EXECUTE\') AS allowed',(signature,))
                    need(c.fetchone()['allowed'] is False,'CHATGPT_AGENT_SQL_ALLOWED')
            c.execute('SELECT bridge.cg_managed_status(%s) AS result',(CANARY,))
            need(c.fetchone()['result']['registered'] is False,'CANARY_ENROLLED')
        conn.rollback()
    with ag._connect() as conn:
        with conn.cursor() as c:
            c.execute('SET TRANSACTION READ ONLY')
            c.execute('SELECT session_user AS actor')
            need(c.fetchone()['actor']=='bridge_agent','AGENT_ROLE_WRONG')
            c.execute('SELECT bridge.ag_dispatch_list(0,50) AS result')
            need(c.fetchone()['result']==[],'AGENT_DISPATCH_NONEMPTY')
            c.execute('SELECT has_table_privilege(session_user,\'bridge.managed_dialogs\',\'SELECT\') AS allowed')
            need(c.fetchone()['allowed'] is False,'AGENT_DIRECT_TABLE_ACCESS')
            if installed:
                for signature in ('bridge.ag_view_managed_dialog(text,integer,integer)',
                                  'bridge.ag_managed_delivery_status(text,bigint)',
                                  'bridge.ag_managed_report_status(text,text)'):
                    c.execute('SELECT has_function_privilege(session_user,%s,\'EXECUTE\') AS allowed',(signature,))
                    need(c.fetchone()['allowed'] is True,'AGENT_MANAGED_API_DENIED')
                for statement,params in (
                    ('SELECT bridge.ag_view_managed_dialog(%s,0,10)',(CANARY,)),
                    ('SELECT bridge.ag_managed_delivery_status(%s,5)',(CANARY,)),
                    ('SELECT bridge.ag_managed_report_status(%s,%s)',(CANARY,'pgv2-stage33f-sdk-report-v1')),
                ):
                    c.execute('SAVEPOINT denycheck')
                    try:
                        c.execute(statement,params)
                        raise Stop('CANARY_LEAK_THROUGH_MANAGED_READ')
                    except Stop:raise
                    except Exception as exc:
                        need(getattr(exc,'sqlstate',None)=='42501','UNEXPECTED_MANAGED_DENIAL')
                    c.execute('ROLLBACK TO SAVEPOINT denycheck')
        conn.rollback()
    print('STAGE34C_REAL_ACTOR_MANAGED_READ_ACL_VERIFIED')
    print('STAGE34C_UNMANAGED_CANARY_DENIED_BY_NEW_API')
    print('STAGE34C_MANAGED_DISPATCH_EMPTY_CANARY_UNCHANGED')

def check_candidate():
    pin()
    need(trusted(DST),'CANDIDATE_MISSING_OR_UNTRUSTED')
    need(DST.read_text('utf-8')==render(SRC.read_text('utf-8')),
         'CANDIDATE_CHANGED_FROM_PINNED_RENDER')
    need(sha(CONFIG.read_bytes())==CONFIG_SHA and sha(SRC.read_bytes())==CURRENT_SHA,
         'LIVE_SOURCES_MUTATED')
    need('const dryRun = true;' in DST.read_text('utf8') and
         'const taskIds = null;' in DST.read_text('utf8'),'CANDIDATE_NOT_INERT')
    print('STAGE34C_DRYRUN_CANDIDATE_PINNED_NOT_REGISTERED')

def main():
    try:
        need(len(sys.argv)==2 and sys.argv[1] in ('preflight','post_sql','verify'),
             'INVALID_MODE')
        mode=sys.argv[1]
        pin() if mode=='preflight' else None
        check_database(mode!='preflight')
        if mode=='preflight':
            need(not DST.exists() and not DST.is_symlink(),'CANDIDATE_ALREADY_PRESENT')
            print('STAGE34C_READ_ONLY_PREFLIGHT_OK')
        elif mode=='post_sql':
            print('STAGE34C_AGENT_NARROW_READ_FUNCTIONS_READY')
        else:
            check_candidate()
            C,A=get_actors();ag=A()
            result=dispatch(parse(['list','--offset','0','--limit','20']),ag)
            need(result['dialogs']==[],'INERT_MANAGED_CLI_SEES_TASKS')
            result=dispatch(parse(['health']),ag)
            need(result['read_only'] is True and result['actor_role']=='bridge_agent',
                 'MANAGED_CLI_ACTOR_WRONG')
            print('STAGE34C_REAL_AGENT_CLI_READONLY_FORMAT_OK')
            print('STAGE34C_NO_MANAGED_DISPATCH_NO_SDK_NO_CUTOVER')
    except Exception as e:
        print('STAGE34C_STOP_'+(str(e) if isinstance(e,Stop) else type(e).__name__),file=sys.stderr)
        return 2
    return 0

if __name__=='__main__':raise SystemExit(main())
