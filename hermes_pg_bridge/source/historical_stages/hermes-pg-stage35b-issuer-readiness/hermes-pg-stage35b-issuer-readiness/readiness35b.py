#!/usr/bin/env python3
"""Stage35B: fail-closed, read-only configuration and legacy OAuth inventory.

No private content, URLs from service configuration, tokens or database payloads
are printed. No service starts, network writes, or SQL mutations.
"""
from __future__ import annotations
import ast, hashlib, json, os, pwd, re, subprocess, sys
from pathlib import Path

HOME=Path('/home/kali')
STAGE35A=HOME/'hermes-pg-stage35a-oauth-managed-shadow'
CURRENT=HOME/'.config/gigatool/opencode.json'
CFG_SHA='e5320f6e971073d5d2c43a64ce85d165cb49dc9a94e6cf7806009ebc2b59ded1'
PILOT='task_bddaadef89b7458fb7f8cf437dc0f023'
CANARY='task_f2c64cfdf7894537b35a19a6def977c6'
PILOT_JS=HOME/'hermes-pg-stage34eb-pilot-candidate/mt-pg-inbox-managed-stage34eb-pilot-unregistered.mjs'
AUTH=HOME/'.config/bridge-pg-v2/managed-oauth35a.json'
PERMIT=HOME/'.config/bridge-pg-v2/managed-mcp-write-permit35b.json'
GATE=HOME/'.config/bridge-pg-v2/managed-writes-stage34d-armed'
UNIT='hermes-pg-stage35a-oauth-shadow.service'

class Stop(Exception): pass

def need(x,msg):
    if not x: raise Stop(msg)

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def svc(name,expected):
    p=subprocess.run(['systemctl','--user','is-active','--quiet',name],capture_output=True,timeout=8)
    need((p.returncode==0)==expected,'SERVICE_STATE_MISMATCH_'+name.split('.')[0])

def preflight():
    need(os.getuid()==os.geteuid()!=0 and pwd.getpwuid(os.getuid()).pw_name=='kali','WRONG_USER')
    need(Path.home()==HOME,'WRONG_HOME')
    need(CURRENT.is_file() and not CURRENT.is_symlink() and sha(CURRENT)==CFG_SHA,'LIVE_CONFIG_DRIFT')
    entries=json.loads(CURRENT.read_text())['plugin']
    need(isinstance(entries,list) and len(entries)==7,'LIVE_PLUGIN_COUNT_DRIFT')
    need(not any('mt-pg-inbox-managed-stage34eb' in str(x) for x in entries),'PILOT_STILL_REGISTERED')
    for p in (AUTH,PERMIT,GATE):need(not p.exists() and not p.is_symlink(),'UNEXPECTED_AUTH_OR_WRITE_GATE')
    for unit in ('hermes-pg-v2-stage33fb-cg.service','hermes-pg-tunnel-stage33fd.service','bridge-pg-v2-ssh-shadow.service'):
        svc(unit,True)
    svc(UNIT,False)
    need((HOME/'.config/systemd/user'/UNIT).is_file(),'SHADOW_UNIT_MISSING')
    sys.path.insert(0,str(STAGE35A))
    from stage35a import read_db
    read_db()
    print('STAGE35B_7_PLUGIN_CANARY_AND_QUIESCED_PILOT_PINNED')
    print('STAGE35B_TWO_ROLE_DATABASE_AND_RECEIPTS_READONLY_OK')
    print('STAGE35B_OAUTH_SHADOW_INACTIVE_NO_CONFIG_NO_WRITES')
    print('STAGE35B_18773_TUNNEL_AND_CANARY_RUNNING_UNCHANGED')
    return True

LEGACY_FILES=[
    HOME/'bridge-pg-v2/bridge_pg/mcp_v2.py',
    HOME/'S960b/research/hermes_chat_bridge/bridge_server.py',
    HOME/'bridge-pg-v2/bridge_pg/mcp_http_v2.py',
]
AUTH_WORDS=re.compile(r'oauth|token|authorize|issuer|require_owner|access_token|refresh|pkce|jwks',re.I)
SENSITIVE=re.compile(r'pass|secret|token|credential|key|cookie',re.I)

def analyze_source(path:Path):
    if not path.is_file() or path.is_symlink():return {'present':False}
    try:
        data=path.read_bytes()
        need(len(data)<=1_000_000,'LEGACY_SOURCE_TOO_LARGE')
        tree=ast.parse(data.decode('utf8'),filename=path.name)
    except (UnicodeError,SyntaxError,ValueError):return {'present':True,'parseable':False}
    top=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]
    syms={n.name for n in top}
    names={n.id for n in ast.walk(tree) if isinstance(n,ast.Name)}
    labels={n.attr for n in ast.walk(tree) if isinstance(n,ast.Attribute)}
    func_calls={n.func.id if isinstance(n.func,ast.Name) else n.func.attr if isinstance(n.func,ast.Attribute) else '' for n in ast.walk(tree) if isinstance(n,ast.Call)}
    return {
      'present':True,'parseable':True,'sha256':hashlib.sha256(data).hexdigest(),
      'has_require_owner':('require_owner' in names or 'require_owner' in syms or 'require_owner' in labels),
      'has_oauth_provider':('BridgeOAuthProvider' in names or 'BridgeOAuthProvider' in syms or 'BridgeOAuthProvider' in labels),
      'has_access_token_handler':bool({'load_access_token','load_authorization_code','exchange_authorization_code'} & (syms|labels|names)),
      'has_jwt_verifier_reference':bool({'jwt','jwt_decode','decode_jwt','PyJWKClient'} & (names|labels|func_calls)),
      'has_issuer_verification_reference':bool({'verify_issuer','issuer','jwt_issuer'} & (names|labels|syms)),
      'function_count':len(top)
    }

def legacy():
    for p in LEGACY_FILES:
        rec=analyze_source(p)
        print('STAGE35B_LEGACY_'+p.name.replace('.','_').upper()+'='+json.dumps(rec,sort_keys=True,separators=(',',':')))
    print('STAGE35B_REQUIRE_OWNER_IS_NOT_PROOF_OF_OAUTH_ISSUER_OR_JWT')
    print('STAGE35B_LEGACY_STATIC_AUDIT_ONLY_NO_CODE_OR_SECRET_DUMP')

def verify():
    preflight()
    print('STAGE35B_READ_ONLY_VERIFY_COMPLETE_NO_TUNNEL_SWITCH')

def main(args):
    try:
        if args==['preflight']:preflight()
        elif args==['legacy']:legacy()
        elif args==['verify']:verify()
        else:raise Stop('INVALID_ACTION')
        return 0
    except Exception as exc:
        msg=str(exc) if isinstance(exc,Stop) else 'UNEXPECTED_'+type(exc).__name__
        print('STAGE35B_STOP_'+msg[:120],file=sys.stderr)
        return 2
if __name__=='__main__':raise SystemExit(main(sys.argv[1:]))
