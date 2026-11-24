#!/usr/bin/env python3
"""Read-only preflight for Stage 33F-D. Never touches SDK, outbox or messages.

Fails closed if expected seven-plugin MT handoff or bounded 33F MCP has drifted.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys
import urllib.request

HOME = Path('/home/kali')
ROOT = HOME/'hermes-pg-stage33fd-tunnel-cutover'
CFG = HOME/'.config/gigatool/opencode.json'
HANDOFF = HOME/'hermes-pg-stage33fc-handoff'
FB = HOME/'hermes-pg-stage33fb-mcp-ready'
PLUGIN = HOME/'hermes-pg-stage33f-sdk-preflight/mt-pg-inbox-agent-stage33f.mjs'
CONFIG_SHA = 'a8f11a25173b5e7f1290d97b4aedfaea24403256fa09c16144093e0f9d297cb3'
PLUGIN_SHA = '3535ae7bd5bb0da3e8574afcab299118dac744bc851c4bcb90b8656acd8b3ea7'
LOGIC_SHA = '7bd588ef646ece1b7c47332692a937b669dce5ea4f98c1efddafc170116f5212'
SERVER_SHA = '9ba3a584d60b08f0e61c5e24ab2d757c56dbc71c416c8186d818692b2102d24f'
CANARY = 'task_f2c64cfdf7894537b35a19a6def977c6'
SERVICE_NEW = 'hermes-pg-tunnel-stage33fd.service'
SERVICE_OLD = 'hermes-pg-tunnel-one-shot.service'
ARM_FILE = FB/'armed-stage33fc'

class Stop(ValueError): pass

def need(ok: bool,code: str):
    if not ok:raise Stop(code)

def digest(b: bytes)->str: return hashlib.sha256(b).hexdigest()

def trusted(path: Path)->bool:
    try:
        st=path.lstat()
        return stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid() and st.st_nlink==1 and not path.is_symlink()
    except OSError:return False

def active(unit: str)->bool:
    r=subprocess.run(['systemctl','--user','is-active','--quiet',unit],
                     capture_output=True,timeout=6)
    return r.returncode==0

def plugin_entry(entry):
    if isinstance(entry,str): return entry,{}
    if isinstance(entry,list) and len(entry)==2 and isinstance(entry[0],str) and isinstance(entry[1],dict):
        return entry[0],entry[1]
    raise Stop('PLUGIN_ENTRY_INVALID')

def validated_config(data: bytes)->bool:
    need(digest(data)==CONFIG_SHA,'LIVE_CONFIG_DRIFT')
    d=json.loads(data)
    entries=d.get('plugin')
    need(isinstance(entries,list) and len(entries)==7,'PLUGIN_COUNT_DRIFT')
    items=[plugin_entry(x) for x in entries]
    pg=[(p,opts) for p,opts in items if Path(p).name.startswith('mt-pg-inbox-')]
    need(len(pg)==1 and pg[0][0]==str(PLUGIN),'EXCLUSIVE_PG_PLUGIN_NOT_FOUND')
    need(pg[0][1].get('dry_run') is False and pg[0][1].get('task_ids')==[CANARY], 'PG_PLUGIN_OPTIONS_DRIFT')
    need(not any(Path(p).name in ('mt-git-inbox.mjs','mt-pg-inbox-canary.mjs') for p,_ in items),'LEGACY_PLUGIN_PRESENT')
    return True

def http_ping(port: int)->bool:
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://127.0.0.1:{port}/ping',timeout=3) as r:
            return r.status==200
    except Exception:return False

def check(phase='preflight'):
    need(os.geteuid()!=0 and pwd.getpwuid(os.geteuid()).pw_name=='kali' and
         Path.home()==HOME and Path.cwd().resolve()==ROOT,'WRONG_CONTEXT')
    for path,sha in ((CFG,CONFIG_SHA),(PLUGIN,PLUGIN_SHA),(FB/'logic33fb.py',LOGIC_SHA),
                     (FB/'cg_mcp33fb.py',SERVER_SHA)):
        need(trusted(path) and digest(path.read_bytes())==sha,'PINNED_FILE_MISMATCH')
    need(validated_config(CFG.read_bytes()),'CONFIG_DENIED')
    need(trusted(HANDOFF/'handoff-result.json'),'HANDOFF_RESULT_UNAVAILABLE')
    handoff=json.loads((HANDOFF/'handoff-result.json').read_text())
    need(handoff.get('outcome')=='committed' and handoff.get('code')=='PG_ONLY_CONFIG_INSTALLED','HANDOFF_NOT_COMMITTED')
    need(not ARM_FILE.exists() and not ARM_FILE.is_symlink(),'CANARY_ALREADY_ARMED')
    need(active('bridge-pg-v2-ssh-shadow.service'),'SSH_SHADOW_OFFLINE')
    need(active('hermes-pg-v2-stage33fb-cg.service') and http_ping(18773),'ROLE_MCP_OFFLINE')
    if phase=='preflight':
        need(active(SERVICE_OLD),'OLD_TUNNEL_OFFLINE')
        need(not active(SERVICE_NEW),'NEW_TUNNEL_ALREADY_ACTIVE')
    elif phase=='switched':
        need(active(SERVICE_NEW) and not active(SERVICE_OLD),'TUNNEL_STATE_INVALID')
    else: raise Stop('BAD_PHASE')
    for name in ('hermes-bridge.service','git-dialog-taskboard.service'):
        need(not active(name),'GIT_SERVICE_RESTARTED')
    sys.path.insert(0,str(FB))
    sys.path.insert(0,str(HOME/'hermes-pg-stage33e-shadow-clients'))
    from actor_import33e import actor_classes
    from logic33fb import status
    C,A=actor_classes()
    cg,ag=C(),A()
    need(cg.health().get('ok') is True and ag.health().get('ok') is True,'ACTOR_DB_ROLE_UNAVAILABLE')
    receipt=status(cg)
    need(receipt['actor_role']=='bridge_chatgpt' and receipt['seq']==4 and
         not receipt['test_task_committed'] and not receipt['test_report_exact'] and
         not receipt['sdk_roundtrip_complete'],'CANARY_PREVIOUSLY_USED_OR_CHANGED')
    need(ag.dialog_metadata(CANARY).get('last_seq')==4,'AGENT_CANARY_VIEW_MISMATCH')
    # Explicitly no delivery claim, no message write, no SDK call.
    print('STAGE33FD_SEVEN_PLUGIN_EXCLUSIVE_HANDOFF_VERIFIED')
    print('STAGE33FD_TWO_REAL_ACTOR_LOGINS_OK')
    print('STAGE33FD_CANARY_SEQ4_UNCHANGED_AND_SEND_NOT_ARMED')
    print('STAGE33FD_SSH_AND_ROLE_MCP_READY')
    print('STAGE33FD_PREFLIGHT_OK' if phase=='preflight' else 'STAGE33FD_POST_SWITCH_READ_ONLY_OK')

if __name__=='__main__':
    try:
        check(sys.argv[1] if len(sys.argv)==2 else 'preflight')
    except Exception as e:
        code=str(e) if isinstance(e,Stop) else type(e).__name__
        print('STAGE33FD_STOP_'+code,file=sys.stderr)
        raise SystemExit(2)
