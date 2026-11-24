#!/usr/bin/env python3
"""Read-only inspection of Stage 34C and 34D invariants, no writes."""
import hashlib
import json
import os
import sys
from pathlib import Path
import render34d as p

ROOT=p.ROOT
ARM=Path('/home/kali/.config/bridge-pg-v2/managed-writes-stage34d-armed')
READ=ROOT/'managed_read34c.py'
CLI=ROOT/'managed_agent_cli34d.py'
CANARY=p.CANARY
READ_SHA='443967becd000dbbedb3cbcac7291b2c3ddbfb3718318976b984ca9130046fac'

class Stop(Exception):pass

def need(test,why):
    if not test:raise Stop(why)

def actor_classes():
    sys.path.insert(0,'/home/kali/hermes-pg-stage33e-shadow-clients')
    from actor_import33e import actor_classes
    return actor_classes()

def check_db():
    ChatGPT,Agent=actor_classes()
    cg,ag=ChatGPT(),Agent()
    for role,instance in (('bridge_chatgpt',cg),('bridge_agent',ag)):
        with instance._connect() as conn:
            with conn.cursor() as c:
                c.execute('SET TRANSACTION READ ONLY')
                c.execute('SELECT session_user AS actor')
                need(c.fetchone()['actor']==role,'ACTOR_IDENTITY_CHANGED')
                c.execute("SELECT count(*) AS n FROM pg_proc p JOIN pg_namespace s ON s.oid=p.pronamespace WHERE s.nspname='bridge'")
                need(c.fetchone()['n']==42,'FUNCTION_COUNT_CHANGED')
                c.execute("SELECT count(*) AS n FROM pg_class c JOIN pg_namespace s ON s.oid=c.relnamespace WHERE s.nspname='bridge' AND c.relkind IN ('r','p')")
                need(c.fetchone()['n']==4,'TABLE_COUNT_CHANGED')
                c.execute('SELECT has_table_privilege(session_user,\'bridge.managed_dialogs\',\'SELECT\') AS allowed')
                need(c.fetchone()['allowed'] is False,'TABLE_ACL_LEAK')
                if role=='bridge_chatgpt':
                    c.execute('SELECT bridge.cg_list_managed_dialogs(0,50) AS result')
                    need(c.fetchone()['result']==[],'MANAGED_REGISTRY_NOT_EMPTY')
                else:
                    c.execute('SELECT bridge.ag_dispatch_list(0,50) AS result')
                    need(c.fetchone()['result']==[],'AGENT_DISPATCH_NOT_EMPTY')
                c.execute('SELECT has_function_privilege(session_user,%s,\'EXECUTE\') AS allowed',
                          ('bridge.ag_claim_managed_message(text,bigint,text,text,integer)',))
                need(c.fetchone()['allowed'] is (role=='bridge_agent'),'AG_CLAIM_ACL_MISMATCH')
                c.execute('SELECT has_function_privilege(session_user,%s,\'EXECUTE\') AS allowed',
                          ('bridge.cg_send_managed_task(text,text,text,bigint)',))
                need(c.fetchone()['allowed'] is (role=='bridge_chatgpt'),'CG_SEND_ACL_MISMATCH')
            conn.rollback()
        meta=instance.dialog_metadata(CANARY)
        need(meta.get('last_seq')==6 and meta.get('state')=='active','CANARY_CHANGED')
        for seq in (5,6):
            need(instance.delivery_status(CANARY,seq).get('status')=='delivered','CANARY_ACK_CHANGED')
    print('STAGE34D_TWO_ACTOR_ROLES_42_FUNCTIONS_AND_EMPTY_REGISTRY_VERIFIED')
    print('STAGE34D_UNMANAGED_CANARY_SEQ6_BOTH_DELIVERED')

def check_files(after):
    p.verify_inputs()
    need(not ARM.exists() and not ARM.is_symlink(),'WRITE_ARMED_BEFORE_APPROVAL')
    need(p.trusted(READ) and p.sha(READ)==READ_SHA,'READ_CLI_COPY_CHANGED')
    need(p.trusted(CLI),'WRITE_CLI_UNTRUSTED')
    need(p.sha(p.CONFIG)==p.CONFIG_SHA,'LIVE_PLUGIN_CONFIG_CHANGED')
    if after:
        need(p.trusted(p.DEST),'CANDIDATE_NOT_CREATED')
        need(p.DEST.read_text()==p.render(p.SOURCE.read_text()),'CANDIDATE_MISMATCH')
        need('const dryRun = true;' in p.DEST.read_text(),'SDK_NOT_LOCKED')
        report=ROOT/'plugin_contract34d.json'
        need(p.trusted(report),'SOURCE_REPORT_MISSING')
        data=json.loads(report.read_text())
        need(data['source_sha256']==p.SOURCE_SHA and len(data['segments'])==5,'SOURCE_REPORT_INVALID')
        print('STAGE34D_INERT_CANDIDATE_PINNED_SOURCE_CONTRACT_COLLECTED')
    else:
        need(not p.DEST.exists(),'CANDIDATE_ALREADY_BUILT')
    print('STAGE34D_LIVE_SEVEN_PLUGIN_CONFIGURATION_UNCHANGED')
    print('STAGE34D_NO_WRITE_ARM_NO_SDK_NO_MANAGED_DISPATCH')

def main(argv):
    try:
        need(len(argv)==1 and argv[0] in ('preflight','verify'),'INVALID_MODE')
        need(os.getuid()==os.geteuid()!=0 and Path.home()==Path('/home/kali') and
             Path.cwd().resolve()==ROOT,'INVALID_CONTEXT')
        check_files(argv[0]=='verify')
        check_db()
        print('STAGE34D_READ_ONLY_'+argv[0].upper()+'_VERIFIED')
        return 0
    except Exception as e:
        err=str(e) if isinstance(e,(Stop,p.Refused)) else type(e).__name__
        print('STAGE34D_STOP_'+err[:90],file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main(sys.argv[1:]))
