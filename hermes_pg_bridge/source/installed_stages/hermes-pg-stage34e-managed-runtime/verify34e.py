#!/usr/bin/env python3
"""Read-only verification of roles, canary receipts and unregistered candidate."""
from __future__ import annotations
import hashlib
import os
import sys
from pathlib import Path
import render34e as r

class Stop(Exception):pass

def need(ok,msg):
    if not ok:raise Stop(msg)

def verify_db():
    sys.path.insert(0,'/home/kali/hermes-pg-stage33e-shadow-clients')
    from actor_import33e import actor_classes
    ChatGPT, Agent=actor_classes()
    for role, klass in [('bridge_chatgpt',ChatGPT),('bridge_agent',Agent)]:
        obj=klass()
        with obj._connect() as conn:
            with conn.cursor() as c:
                c.execute('SET TRANSACTION READ ONLY')
                c.execute('SELECT session_user AS role')
                need(c.fetchone()['role']==role,'db_actor_identity_changed')
                c.execute("SELECT count(*) AS n FROM pg_proc p JOIN pg_namespace s ON s.oid=p.pronamespace WHERE s.nspname='bridge'")
                need(c.fetchone()['n']==42,'expected_42_functions')
                c.execute("SELECT count(*) AS n FROM pg_class c JOIN pg_namespace s ON s.oid=c.relnamespace WHERE s.nspname='bridge' AND c.relkind IN ('r','p')")
                need(c.fetchone()['n']==4,'expected_four_tables')
                if role=='bridge_agent':
                    c.execute('SELECT bridge.ag_dispatch_list(0,50) AS result')
                    need(c.fetchone()['result']==[],'unexpected_managed_dispatch')
                else:
                    c.execute('SELECT bridge.cg_list_managed_dialogs(0,50) AS result')
                    need(c.fetchone()['result']==[],'managed_registry_not_empty')
            conn.rollback()
        m=obj.dialog_metadata(r.CANARY)
        need(m.get('last_seq')==6 and m.get('state')=='active','canary_drifted')
        for seq in (5,6):
            need(obj.delivery_status(r.CANARY,seq).get('status')=='delivered','canary_delivery_changed')
    print('STAGE34E_REAL_ACTOR_ROLES_42_FUNCTIONS_EMPTY_MANAGED_REGISTRY_VERIFIED')
    print('STAGE34E_CANARY_SEQ5_SEQ6_DELIVERED_UNMODIFIED')

def verify_files(after):
    r.preflight()
    need(r.trusted(r.SRC) and r.sha(r.SRC)==r.SOURCE_SHA,'source_pin_changed')
    need(not r.WRITE_ARM.exists(),'write_gate_must_be_absent')
    if after:
        need(r.trusted(r.DEST),'candidate_missing_or_untrusted')
        code=r.render(r.SRC.read_text('utf8'),r.parts(r.PARTS.read_text('utf8')))
        need(code==r.DEST.read_text('utf8'),'candidate_not_exact_patch')
        need('const dryRun = true;' in code,'candidate_sdk_not_forced_off')
        need('mt-pg-inbox-managed-stage34e' in code,'state_not_isolated')
        print('STAGE34E_BYTE_EXACT_PATCH_AND_FORCED_DRYRUN_VERIFIED')
    else:
        need(not r.DEST.exists() and not r.DEST.is_symlink(),'candidate_already_present')
    print('STAGE34E_LIVE_CONFIG_SEVEN_PLUGINS_AND_CANARY_FILTER_UNCHANGED')

def main(argv):
    try:
        need(argv in (['preflight'],['verify']),'invalid_mode')
        verify_files(argv[0]=='verify')
        verify_db()
        print('STAGE34E_'+('READ_ONLY_PREFLIGHT_OK' if argv[0]=='preflight' else 'READ_ONLY_VERIFY_COMPLETE'))
        return 0
    except Exception as e:
        msg=str(e) if isinstance(e,(Stop,r.Refused)) else type(e).__name__
        print('STAGE34E_STOP_'+msg[:90],file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main(sys.argv[1:]))
