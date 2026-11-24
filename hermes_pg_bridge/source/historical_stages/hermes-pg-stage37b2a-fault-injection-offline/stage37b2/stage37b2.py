#!/usr/bin/env python3
"""Stage 37B2A: read-only preflight for isolated fault-injection of pinned JS.

No write capabilities, no SDK execution, no service controls and no DB writes.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pwd
import stat
import sys
from pathlib import Path

HOME=Path('/home/kali')
ROOT=HOME/'hermes-pg-stage37b2-fault-injection'
B1=HOME/'hermes-pg-stage37b-readonly-restart/stage37b.py'
B1_AFTER=HOME/'hermes-pg-stage37b-readonly-restart/after.json'
CANDIDATE=HOME/'hermes-pg-stage34eb-pilot-candidate/mt-pg-inbox-managed-stage34eb-pilot-unregistered.mjs'
CANDIDATE_SHA='b3679086583d77c96b6ed1c3dc7b1ed1656707dabba4d3ac74843fe64177d730'
MCP_PERMIT=HOME/'.config/bridge-pg-v2/managed-mcp-write-permit35c.json'
AG_GATE=HOME/'.config/bridge-pg-v2/managed-writes-stage34d-armed'
REPORT='PGV2_STAGE36_E2E_ACK_20261123_B6C26A'

class Stop(Exception):pass

def need(ok:bool,code:str):
    if not ok: raise Stop('STAGE37B2_STOP_'+code)

def digest(raw:bytes)->str:
    return hashlib.sha256(raw).hexdigest()

def private_file(p:Path,max_size:int=2_000_000)->bytes:
    st=p.lstat()
    need(stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid() and
         st.st_nlink==1 and (st.st_mode & 0o077)==0 and 0<st.st_size<=max_size,
         'UNTRUSTED_PRIVATE_FILE_'+p.name.replace('.','_'))
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        current=os.fstat(fd)
        need((current.st_dev,current.st_ino)==(st.st_dev,st.st_ino),'FILE_RACE')
        raw=os.read(fd,max_size+1)
        need(len(raw)==st.st_size,'FILE_CHANGED')
        return raw
    finally:
        os.close(fd)

def check_source(blob:bytes)->bool:
    markers=(
        'function managedReportId(',
        'function confirmedMtResponseAfter(',
        'async function reconcileAnsweredInflight(',
        'export async function runTick(ctx) {',
        'for (const taskId of Object.keys(state.inflight).sort())',
        'const dialogs = await listAll(ctx.cliCall)',
        'managed_inflight_awaits_receipt',
        'managed_inflight_ack_confirmed',
        'state.inflight[taskId]',
        'ctx.taskIds.includes(dialog?.task_id)',
        'saveState(ctx.paths, state);',
        'const dryRun = false;',
    )
    try:text=blob.decode('utf8')
    except UnicodeDecodeError:return False
    if digest(blob)!=CANDIDATE_SHA:return False
    return all(text.count(v)>=1 for v in markers) and text.index(markers[4])<text.index(markers[5])

def load_b1():
    need(B1.exists() and not B1.is_symlink(),'STAGE37B1_NOT_INSTALLED')
    spec=importlib.util.spec_from_file_location('stage37b1_pinned',B1)
    need(spec is not None and spec.loader is not None,'B1_IMPORT_INVALID')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def preflight(*,check_installed=False):
    need(os.getuid()==os.geteuid()!=0 and pwd.getpwuid(os.getuid()).pw_name=='kali' and
         Path.home()==HOME and Path(os.environ.get('HOME'))==HOME,'WRONG_USER')
    b1=load_b1()
    b1.user_check()
    b1.absent_active_capabilities()
    need(b1.systemd_timer_active(),'STAGE37A_WATCHDOG_INACTIVE')
    config_sha=b1.config_snapshot()
    state=b1.state_snapshot()
    db=b1.pg_snapshot()  # SET TRANSACTION READ ONLY
    processes,unknown=b1.multitool_processes()
    need(len(processes)>0 and unknown==0,'MULTITOOL_NOT_HEALTHY')
    marker=json.loads(private_file(B1_AFTER,16_384))
    need(marker.get('phase')=='post_restart' and marker.get('config_sha256')==config_sha,
         'STAGE37B1_AFTER_MISSING_OR_CONFIG_CHANGED')
    need(state.get('seen')>=1 and state.get('inflight_empty') is True,'STAGE37B1_STATE_CHANGED')
    need(db['last_seq']==2 and db['dispatch_enabled'] is False,'STAGE37B1_DB_CHANGED')
    st=CANDIDATE.lstat()
    need(stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid() and
         st.st_nlink==1 and not CANDIDATE.is_symlink() and 0<st.st_size<200_000,
         'PLUGIN_CANDIDATE_UNTRUSTED')
    candidate=CANDIDATE.read_bytes()
    need(check_source(candidate),'PINNED_CANDIDATE_CHANGED_OR_MISSING_ANCHORS')
    need(not MCP_PERMIT.exists() and not MCP_PERMIT.is_symlink() and
         not AG_GATE.exists() and not AG_GATE.is_symlink(),'WRITE_CAPABILITIES_APPEARED')
    if check_installed:
        st=ROOT.lstat()
        need(stat.S_ISDIR(st.st_mode) and st.st_uid==os.geteuid() and
             not ROOT.is_symlink() and not (st.st_mode&0o077),'INSTALL_DIRECTORY_UNTRUSTED')
    print('STAGE37B2_PLUGIN_SOURCE_PINNED_'+CANDIDATE_SHA[:12])
    print('STAGE37B2_STAGE37B1_RESTART_BASELINE_RECONFIRMED')
    print('STAGE37B2_DB_SEQ1_SEQ2_DELIVERED_ONCE_READONLY')
    print('STAGE37B2_NO_WRITE_PERMIT_NO_AGENT_GATE_DISPATCH_FALSE')
    print('STAGE37B2_MT_PROCESS_COUNT',len(processes))
    print('STAGE37B2_OFFLINE_FAULT_SIM_READY')

def main():
    try:
        need(len(sys.argv)==2 and sys.argv[1] in ('preflight','status'),'USAGE')
        preflight(check_installed=sys.argv[1]=='status')
        return 0
    except Exception as e:
        print(str(e) if isinstance(e,Stop) else 'STAGE37B2_STOP_'+type(e).__name__,file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main())
