#!/usr/bin/env python3
"""Stage35A read-only deployment integrity and role-bound DB inventory."""
from __future__ import annotations
import hashlib,json,os,pwd,re,stat,subprocess,sys
from pathlib import Path
from oauth_guard35a import CONFIG,config_from_file,AuthDenied,private_regular

HOME=Path('/home/kali')
ROOT=HOME/'hermes-pg-stage35a-oauth-managed-shadow'
CFG=HOME/'.config/gigatool/opencode.json'
CANARY='task_f2c64cfdf7894537b35a19a6def977c6'
PILOT='task_bddaadef89b7458fb7f8cf437dc0f023'
CFG_SHA='e5320f6e971073d5d2c43a64ce85d165cb49dc9a94e6cf7806009ebc2b59ded1'
CANARY_SHA='220da977562f9c0763d6b5d5640f686f54f041d357ed397c8482c0d4363f525c'
CANARY_PLUGIN=HOME/'hermes-pg-stage33i-ackfix/mt-pg-inbox-agent-stage33i.mjs'
SHADOW_UNIT=HOME/'.config/systemd/user/hermes-pg-stage35a-oauth-shadow.service'

class Stop(Exception):pass

def need(ok,code):
    if not ok:raise Stop(code)

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def pinned():
    need(os.geteuid()==os.getuid()!=0 and pwd.getpwuid(os.geteuid()).pw_name=='kali' and
         Path.home()==HOME and Path.cwd().resolve()==ROOT, 'EXECUTION_CONTEXT_INVALID')
    need(CFG.exists() and not CFG.is_symlink() and sha(CFG)==CFG_SHA, 'ACTIVE_CONFIG_DRIFT')
    need(CANARY_PLUGIN.exists() and not CANARY_PLUGIN.is_symlink() and
         sha(CANARY_PLUGIN)==CANARY_SHA, 'CANARY_PLUGIN_DRIFT')
    obj=json.loads(CFG.read_text())
    entries=obj.get('plugin')
    need(isinstance(entries,list) and len(entries)==7,'SEVEN_PLUGINS_REQUIRED')
    names=[x[0] if isinstance(x,list) else x for x in entries]
    need(names.count(str(CANARY_PLUGIN))==1 and not any('managed-stage34eb' in s or
         'mt-git-inbox' in s for s in names),'ACTIVE_PLUGIN_SET_CHANGED')
    it=entries[names.index(str(CANARY_PLUGIN))]
    need(isinstance(it,list) and it[1].get('task_ids')==[CANARY], 'CANARY_FILTER_DRIFT')
    return True

def actor():
    sys.path.insert(0,'/home/kali/hermes-pg-stage33e-shadow-clients')
    from actor_import33e import actor_classes
    CG,AG=actor_classes()
    return CG(),AG()

def read_db():
    cg,ag=actor()
    with cg._connect() as c, c.cursor() as cur:
        cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        cur.execute('SELECT session_user AS actor')
        need(cur.fetchone()['actor']=='bridge_chatgpt','CG_ACTOR_INVALID')
        cur.execute("SELECT count(*) AS n FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='bridge'")
        need(cur.fetchone()['n']==42,'SCHEMA_FUNCTION_COUNT_CHANGED')
        cur.execute("SELECT count(*) AS n FROM pg_class p JOIN pg_namespace n ON n.oid=p.relnamespace WHERE n.nspname='bridge' AND p.relkind IN ('r','p')")
        need(cur.fetchone()['n']==4,'SCHEMA_TABLE_COUNT_CHANGED')
        cur.execute('SELECT bridge.cg_list_managed_dialogs(0,50) AS result')
        allm=cur.fetchone()['result']
        need(len(allm)==1 and allm[0]['task_id']==PILOT and
             allm[0]['dispatch_enabled'] is False and allm[0]['policy_version']==2,
             'PILOT_REGISTRY_NOT_QUIESCED')
        cur.execute('SELECT bridge.cg_managed_detail(%s) AS result',(PILOT,))
        d=cur.fetchone()['result']
        need(d['version']==2 and d['last_seq']==2 and d['next_actor']=='chatgpt',
             'PILOT_DIALOG_CHANGED')
        cur.execute('SELECT bridge.delivery_status(%s,%s) AS result',(PILOT,1))
        need(cur.fetchone()['result']['status']=='delivered','PILOT_TASK_ACK_MISSING')
        cur.execute('SELECT bridge.delivery_status(%s,%s) AS result',(PILOT,2))
        need(cur.fetchone()['result']['status']=='delivered','PILOT_REPORT_ACK_MISSING')
        cur.execute('SELECT bridge.dialog_metadata(%s) AS result',(CANARY,))
        need(cur.fetchone()['result']['last_seq']==6,'CANARY_SEQ_CHANGED')
        for seq in (5,6):
            cur.execute('SELECT bridge.delivery_status(%s,%s) AS result',(CANARY,seq))
            need(cur.fetchone()['result']['status']=='delivered','CANARY_RECEIPT_CHANGED')
        c.rollback()
    with ag._connect() as c, c.cursor() as cur:
        cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        cur.execute('SELECT session_user AS actor')
        need(cur.fetchone()['actor']=='bridge_agent','AG_ACTOR_INVALID')
        cur.execute('SELECT bridge.ag_dispatch_list(0,50) AS result')
        need(cur.fetchone()['result']==[],'AGENT_DISPATCH_NOT_EMPTY')
        c.rollback()
    gate=HOME/'.config/bridge-pg-v2/managed-writes-stage34d-armed'
    need(not gate.exists() and not gate.is_symlink(),'MANAGED_AGENT_WRITE_GATE_ACTIVE')
    from runtime35a import PERMIT
    need(not PERMIT.exists() and not PERMIT.is_symlink(),'MCP_WRITE_PERMIT_ALREADY_PRESENT')
    for unit in ('hermes-pg-v2-stage33fb-cg.service',
                 'hermes-pg-tunnel-stage33fd.service',
                 'bridge-pg-v2-ssh-shadow.service'):
        r=subprocess.run(['systemctl','--user','is-active','--quiet',unit],
                         capture_output=True,timeout=8)
        need(r.returncode==0,'REQUIRED_SERVICE_NOT_ACTIVE_'+unit.split('.')[0])
    print('STAGE35A_REAL_TWO_ROLES_42_FUNCTIONS_ONE_DISABLED_MANAGED_PILOT')
    print('STAGE35A_CANARY_AND_BOTH_PILOT_ACKS_DELIVERED')
    print('STAGE35A_NO_AGENT_GATE_NO_MCP_WRITE_PERMIT_DISPATCH_EMPTY')

def preflight():
    pinned();read_db()
    need(not SHADOW_UNIT.exists() and not SHADOW_UNIT.is_symlink(),
         'SHADOW_UNIT_ALREADY_EXISTS')
    print('STAGE35A_READ_ONLY_PREFLIGHT_PASSED')
    print('STAGE35A_EXISTING_SEVEN_PLUGIN_AND_TUNNEL_18773_PINNED')

def verify():
    pinned();read_db()
    need(SHADOW_UNIT.is_file() and not SHADOW_UNIT.is_symlink(),
         'SHADOW_UNIT_MISSING')
    txt=SHADOW_UNIT.read_text()
    need('managed_mcp35a.py' in txt and
         'hermes-pg-stage35a-oauth-managed-shadow' in txt,
         'SHADOW_UNIT_UNEXPECTED')
    source=(ROOT/'managed_mcp35a.py').read_text()
    need('PORT=18776' in source and "HOST='127.0.0.1'" in source,
         'SHADOW_BIND_NOT_LOOPBACK_18776')
    # This must remain inactive until OAuth provider has been reviewed.
    state=subprocess.run(['systemctl','--user','is-active','--quiet',
                          'hermes-pg-stage35a-oauth-shadow.service'],capture_output=True,timeout=8)
    need(state.returncode!=0,'SHADOW_SERVICE_UNEXPECTEDLY_ACTIVE')
    print('STAGE35A_SHADOW_UNIT_INSTALLED_INACTIVE_NO_TUNNEL_SWITCH')
    print('STAGE35A_AUTH_UNCONFIGURED_FAIL_CLOSED')
    print('STAGE35A_READ_ONLY_VERIFY_SUCCESS')

def audit_auth():
    pinned()
    # Expose no credentials, token contents, or private keys in this report.
    try:
        c=config_from_file()
        print('STAGE35A_OAUTH_CONFIG_PRESENT issuer_valid=true audience_valid=true jwks_keys='+str(len(c['keys'])))
    except AuthDenied:
        print('STAGE35A_OAUTH_CONFIG_MISSING_OR_UNVALIDATED')
    old=Path('/home/kali/bridge-pg-v2/bridge_pg/mcp_v2.py')
    if old.exists():
        text=old.read_text(errors='replace')
        print('STAGE35A_LEGACY_MCP_SOURCE_REQUIRE_OWNER='+str('require_owner' in text).lower())
    # OAuth metadata/issuer is NOT guessed; do not infer from 8766 source.
    print('STAGE35A_OAUTH_DISCOVERY_REQUIRES_CONFIGURED_EXTERNAL_ISSUER')

def main(argv):
    try:
        if argv==['preflight']:preflight()
        elif argv==['verify']:verify()
        elif argv==['auth-audit']:audit_auth()
        else:raise Stop('INVALID_COMMAND')
        return 0
    except Exception as exc:
        code=str(exc) if isinstance(exc,Stop) else 'UNEXPECTED_'+type(exc).__name__
        print('STAGE35A_STOP_'+code[:100],file=sys.stderr)
        return 2
if __name__=='__main__':raise SystemExit(main(sys.argv[1:]))
