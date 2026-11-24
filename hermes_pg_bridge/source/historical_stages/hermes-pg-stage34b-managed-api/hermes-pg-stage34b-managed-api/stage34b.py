#!/usr/bin/env python3
"""Stage 34B: strict actor-identity checks and ROLLBACK-only managed policy test.

The positive test creates/activates/sends to one TEMPORARY managed dialog in
ONE uncommitted database transaction. It always rolls that transaction back.
No SDK, no agent claim, no live dispatch changes.
"""
from __future__ import annotations
import os
import sys
import uuid
from pathlib import Path

ROOT=Path('/home/kali/hermes-pg-stage34b-managed-api')
PRIOR=Path('/home/kali/hermes-pg-stage34a-managed-registry')
CANARY='task_f2c64cfdf7894537b35a19a6def977c6'

class Stop(Exception):pass

def require(cond,code):
    if not cond:raise Stop(code)

def actors():
    sys.path.insert(0,'/home/kali/hermes-pg-stage33e-shadow-clients')
    from actor_import33e import actor_classes
    return actor_classes()

def pin_environment():
    require(os.geteuid()==os.getuid() and os.geteuid()!=0 and
            Path.home()==Path('/home/kali') and Path.cwd().resolve()==ROOT,
            'BAD_EXECUTION_CONTEXT')
    sys.path.insert(0,str(PRIOR))
    from registry34a import pin_runtime, verify_canary
    pin_runtime()
    ChatGPT,Agent=actors()
    verify_canary(ChatGPT,Agent)
    return ChatGPT,Agent

def db_integrity(cg,ag,expect_installed):
    with cg._connect() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT session_user AS actor')
            require(cur.fetchone()['actor']=='bridge_chatgpt','BAD_CHATGPT_IDENTITY')
            cur.execute("SELECT count(*) AS n FROM pg_proc p JOIN pg_namespace s ON s.oid=p.pronamespace WHERE s.nspname='bridge'")
            require(cur.fetchone()['n']==(39 if expect_installed else 30),'BAD_FUNCTION_COUNT')
            cur.execute("SELECT count(*) AS n FROM pg_class c JOIN pg_namespace s ON s.oid=c.relnamespace WHERE s.nspname='bridge' AND c.relkind IN ('r','p')")
            require(cur.fetchone()['n']==4,'BAD_TABLE_COUNT')
            cur.execute("SELECT count(*) AS n FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='bridge' AND c.relname='managed_dialogs' AND a.attname='policy_version' AND NOT a.attisdropped")
            require(cur.fetchone()['n']==int(expect_installed),'POLICY_COLUMN_STATE_UNEXPECTED')
            cur.execute('SELECT bridge.cg_managed_status(%s) AS result',(CANARY,))
            require(cur.fetchone()['result'].get('registered') is False,'CANARY_ENROLLED')
            if expect_installed:
                cur.execute('SELECT bridge.cg_list_managed_dialogs(0,50) AS result')
                require(cur.fetchone()['result']==[],'MANAGED_REGISTRY_NOT_EMPTY')
                cur.execute('SELECT bridge.cg_managed_detail(%s) AS result',(CANARY,))
                require(cur.fetchone()['result'].get('registered') is False,'UNMANAGED_DETAIL_LEAK')
                cur.execute("SELECT has_function_privilege(session_user,'bridge.ag_read_managed_message(text,bigint,text)','EXECUTE') AS allowed")
                require(cur.fetchone()['allowed'] is False,'CROSS_ROLE_EXEC_LEAK_CG')
            cur.execute("SELECT has_table_privilege(session_user,'bridge.managed_dialogs','SELECT') AS allowed")
            require(cur.fetchone()['allowed'] is False,'DIRECT_TABLE_GRANT_LEAK_CG')
        conn.rollback()
    with ag._connect() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT session_user AS actor')
            require(cur.fetchone()['actor']=='bridge_agent','BAD_AGENT_IDENTITY')
            cur.execute('SELECT bridge.ag_dispatch_list(0,50) AS result')
            require(cur.fetchone()['result']==[],'AGENT_DISPATCH_NOT_EMPTY')
            cur.execute("SELECT has_table_privilege(session_user,'bridge.managed_dialogs','SELECT') AS allowed")
            require(cur.fetchone()['allowed'] is False,'DIRECT_TABLE_GRANT_LEAK_AGENT')
            if expect_installed:
                cur.execute("SELECT has_function_privilege(session_user,'bridge.cg_send_managed_task(text,text,text,bigint)','EXECUTE') AS allowed")
                require(cur.fetchone()['allowed'] is False,'CROSS_ROLE_EXEC_LEAK_AGENT')
        conn.rollback()

def rollback_only_proof(ChatGPT,Agent):
    cg=ChatGPT();ag=Agent()
    request_id='stage34b_rollback_'+uuid.uuid4().hex
    title='Stage34B rollback-only API proof, not an SDK task'
    task_body='TEST_ONLY_NO_EXECUTION_STAGE34B'
    with cg._connect() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout='12000ms'")
                cur.execute('SELECT bridge.cg_create_managed_dialog(%s,%s) AS result',(request_id,title))
                r=cur.fetchone()['result']
                require(r.get('created') is True and r.get('managed') is True and
                        r.get('dispatch_enabled') is False,'ROLLBACK_CREATE_FAILED')
                task=r['task_id']
                # Disabled-by-default must reject all task submissions.
                cur.execute('SAVEPOINT before_denied_send')
                try:
                    cur.execute('SELECT bridge.cg_send_managed_task(%s,%s,%s,%s) AS result',
                                (task,'stage34b-probe-task',task_body,0))
                    raise Stop('DISABLED_SEND_NOT_BLOCKED')
                except Stop:
                    raise
                except Exception as exc:
                    require(getattr(exc,'sqlstate',None)=='42501','DISABLED_SEND_UNEXPECTED_ERROR')
                cur.execute('ROLLBACK TO SAVEPOINT before_denied_send')
                cur.execute('SELECT bridge.cg_managed_detail(%s) AS result',(task,))
                detail=cur.fetchone()['result']
                require(detail.get('policy_version')==0 and detail.get('version')==0 and
                        detail.get('dispatch_enabled') is False,'MANAGED_INITIAL_STATE_WRONG')
                cur.execute('SELECT bridge.cg_set_managed_dispatch(%s,%s,%s,%s) AS result',
                            (task,True,0,0))
                enabled=cur.fetchone()['result']
                require(enabled.get('changed') is True and enabled.get('policy_version')==1 and
                        enabled.get('dispatch_enabled') is True,'ENABLE_POLICY_FAILED')
                cur.execute('SELECT bridge.cg_send_managed_task(%s,%s,%s,%s) AS result',
                            (task,'stage34b-probe-task',task_body,0))
                sent=cur.fetchone()['result']
                require(sent.get('created') is True and sent.get('seq')==1,'MANAGED_SEND_FAILED')
                cur.execute('SELECT bridge.cg_view_managed_dialog(%s,%s,%s) AS result',(task,0,5))
                view=cur.fetchone()['result']
                require(view.get('last_seq')==1 and len(view.get('messages',[]))==1 and
                        view['messages'][0].get('body')==task_body,'MANAGED_VIEW_FAILED')
                cur.execute('SELECT bridge.cg_set_managed_dispatch(%s,%s,%s,%s) AS result',
                            (task,False,1,1))
                disabled=cur.fetchone()['result']
                require(disabled.get('changed') is True and disabled.get('policy_version')==2,
                        'DISABLE_POLICY_FAILED')
                cur.execute('SELECT bridge.cg_list_managed_dialogs(%s,%s) AS result',(0,10))
                listed=cur.fetchone()['result']
                require(len(listed)==1 and listed[0]['task_id']==task and
                        listed[0]['dispatch_enabled'] is False,'MANAGED_LIST_FAILED')
        finally:
            # Only durable change within this test is ROLLBACK itself.
            conn.rollback()
    require(cg.creation_status(request_id) is None,'ROLLBACK_FAILED_CREATED_DIALOG')
    db_integrity(cg,ag,True)
    print('STAGE34B_ROLLBACK_ONLY_POLICY_AND_SEND_PROOF_OK')
    print('STAGE34B_NO_MANAGED_DIALOG_OR_TASK_COMMITTED')

def main():
    mode=sys.argv[1] if len(sys.argv)==2 else ''
    try:
        ChatGPT,Agent=pin_environment()
        require(mode in ('preflight','proof','verify'),'INVALID_MODE')
        db_integrity(ChatGPT(),Agent(),mode!='preflight')
        if mode=='preflight':
            print('STAGE34B_READ_ONLY_PRE_MIGRATION_OK')
        elif mode=='proof':
            rollback_only_proof(ChatGPT,Agent)
        else:
            print('STAGE34B_ROLE_BOUND_MANAGED_API_READ_ONLY_VERIFIED')
            print('STAGE34B_NO_DISPATCH_ENABLED_AND_CANARY_PRESERVED')
    except Exception as e:
        code=str(e) if isinstance(e,Stop) else type(e).__name__
        print('STAGE34B_STOP_'+code[:90],file=sys.stderr)
        return 2
    return 0

if __name__=='__main__':raise SystemExit(main())
