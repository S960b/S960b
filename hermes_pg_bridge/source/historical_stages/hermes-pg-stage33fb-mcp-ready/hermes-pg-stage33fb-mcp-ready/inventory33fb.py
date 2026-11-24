#!/usr/bin/env python3
"""Safe runtime inventory: no process args, environment, secret paths or DB writes."""
from __future__ import annotations
import hashlib
import json
import os
import pwd
import stat
import subprocess
import sys
from pathlib import Path
from urllib.request import build_opener,ProxyHandler

from logic33fb import CANARY,TASK_MID,REPORT_MID,status
from prepare_cutover33fb import CFG,ROOT,CFG_SHA,SRC,PLUGIN_SHA,OLD, trusted


def need(ok, msg):
    if not ok: raise ValueError(msg)


def unit(name):
    try:
        p=subprocess.run(['systemctl','--user','is-active',name],text=True,
                         capture_output=True,timeout=5)
        return 'active' if p.returncode == 0 and p.stdout.strip() == 'active' else 'inactive'
    except Exception:return 'unknown'


def ping(port):
    try:
        op=build_opener(ProxyHandler({}))
        with op.open(f'http://127.0.0.1:{port}/ping',timeout=3) as r:
            return r.status == 200
    except Exception:return False


def processes():
    out=[]
    uid=os.geteuid()
    for d in Path('/proc').iterdir():
        if not d.name.isdecimal():continue
        try:
            s=d.stat()
            if s.st_uid != uid:continue
            comm=(d/'comm').read_text('utf-8').strip()
            name=comm.lower()
            if not any(q in name for q in ('multitool','gigatool','opencode','node')):continue
            data=(d/'stat').read_text('utf-8')
            tail=data[data.rfind(')')+2:].split()
            out.append({'pid':int(d.name),'comm':comm[:30],
                        'ppid':int(tail[1]),'pgrp':int(tail[2]),
                        'sid':int(tail[3]),'start_ticks':int(tail[19])})
        except Exception:continue
    out.sort(key=lambda p:p['pid'])
    return out[:128]


def run(require_service=False):
    need(os.geteuid()!=0 and pwd.getpwuid(os.geteuid()).pw_name=='kali' and
         Path.cwd().resolve()==ROOT, 'wrong_context')
    need(trusted(CFG) and trusted(SRC),'untrusted_files')
    need(hashlib.sha256(CFG.read_bytes()).hexdigest()==CFG_SHA,'live_config_modified')
    need(hashlib.sha256(SRC.read_bytes()).hexdigest()==PLUGIN_SHA,'sdk_candidate_modified')
    need(unit('bridge-pg-v2-ssh-shadow.service')=='active','shadow_ssh_offline')
    need(unit('hermes-pg-tunnel-one-shot.service')=='active','original_tunnel_changed')
    need(unit('hermes-pg-v2-shadow-cg.service')=='active','shadow_readonly_cg_offline')
    sys.path.insert(0,'/home/kali/hermes-pg-stage33e-shadow-clients')
    from actor_import33e import actor_classes
    C,A=actor_classes()
    cg,ag=C(),A()
    need(cg.health().get('ok') is True and ag.health().get('ok') is True,
         'actor_db_not_ready')
    status_data=status(cg)
    need(status_data['seq']==4 and status_data['test_task_committed'] is False,
         'canary_not_ready_for_test')
    need(ping(18772),'shadow_readonly_mcp_offline')
    need(not (ROOT/'armed-stage33fc').exists(), 'stage33fc_armed_too_early')
    if require_service:
        need(unit('hermes-pg-v2-stage33fb-cg.service')=='active','stage33fb_mcp_offline')
        need(ping(18773),'stage33fb_mcp_ping_failed')
    procs=processes()
    snap=ROOT/'opencode-original-snapshot.json'
    draft=ROOT/'opencode-stage33f-draft.json'
    if require_service:
        need(snap.is_file() and draft.is_file(),'config_draft_missing')
        need(snap.read_bytes()==CFG.read_bytes(),'original_snapshot_changed')
    return {
        'stage':'33F-B', 'safe_state':status_data,
        'runtime_processes':procs, 'process_count':len(procs),
        'candidate_plugin_sha256':PLUGIN_SHA,
        'original_config_sha256':CFG_SHA,
        'draft_config_present':draft.is_file(),
        'service_status':{n:unit(n) for n in (
            'bridge-pg-v2-ssh-shadow.service',
            'hermes-pg-tunnel-one-shot.service',
            'hermes-pg-v2-shadow-cg.service',
            'hermes-pg-v2-stage33fb-cg.service')},
        'mcp_18773_local_ping':ping(18773),
        'mt_sdk_prompt_attempted':False,
        'db_writes_performed':False,
        'original_config_changed':False,
        'new_mcp_attached_to_tunnel':False,
        'exclusive_dispatcher_cutover_pending':True}


def main():
    try:
        check= '--installed' in sys.argv
        info=run(check)
        print(json.dumps(info,ensure_ascii=False,indent=2,sort_keys=True))
        print('STAGE33FB_ROLE_BOUND_MCP_READY_LOCAL_ONLY' if check else 'STAGE33FB_PG_AND_ACTOR_ROLES_PREFLIGHT_OK')
        print('STAGE33FB_RUNTIME_PROCESS_INVENTORY_COLLECTED_NO_RESTART')
        return 0
    except Exception as e:
        print('STAGE33FB_STOP_'+type(e).__name__,file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main())
