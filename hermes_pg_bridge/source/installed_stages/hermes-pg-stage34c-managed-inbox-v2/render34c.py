#!/usr/bin/env python3
"""Render unregistered, forced-dry-run managed MT Inbox from PINNED live 33I plugin."""
from __future__ import annotations
import hashlib
import json
import os
import stat
from pathlib import Path

ROOT=Path('/home/kali/hermes-pg-stage34c-managed-inbox-v2')
SRC=Path('/home/kali/hermes-pg-stage33i-ackfix/mt-pg-inbox-agent-stage33i.mjs')
DST=ROOT/'mt-pg-inbox-managed-stage34c.mjs'
CLI=ROOT/'managed_agent_cli34c.py'
CONFIG=Path('/home/kali/.config/gigatool/opencode.json')
CURRENT_SHA='220da977562f9c0763d6b5d5640f686f54f041d357ed397c8482c0d4363f525c'
CONFIG_SHA='e5320f6e971073d5d2c43a64ce85d165cb49dc9a94e6cf7806009ebc2b59ded1'
CANARY='task_f2c64cfdf7894537b35a19a6def977c6'
OLD_CLI='/home/kali/hermes-pg-stage33i-ackfix/stage33i_agent_cli.py'
OLD_FILTER='const taskIds = Object.freeze(["'+CANARY+'"]);'
OLD_DRY='const dryRun = false; // STAGE33F PROSPECTIVE ONLY: NOT REGISTERED'
OLD_STATE='const STATE_DIR = join(HOME, ".local/state/mt-pg-inbox-agent-stage33f");'
NEW_STATE='const STATE_DIR = join(HOME, ".local/state/mt-pg-inbox-managed-stage34c");'

class Stop(Exception):pass

def need(ok,code):
    if not ok:raise Stop(code)

def sha(data):return hashlib.sha256(data).hexdigest()

def trusted(p):
    try:
        st=p.lstat()
        return stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid() and st.st_nlink==1 and not p.is_symlink()
    except OSError:return False

def replace(s,old,new,count):
    need(s.count(old)==count,'PINNED_SOURCE_LAYOUT_MISMATCH')
    return s.replace(old,new)

def render(source):
    s=source
    s=replace(s,OLD_CLI,str(CLI),2)
    s=replace(s,OLD_FILTER,'const taskIds = null; // STAGE34C ONLY ag_dispatch_list, forced dry-run',1)
    s=replace(s,OLD_DRY,'const dryRun = true; // STAGE34C INERT, options CANNOT ENABLE SDK',1)
    s=replace(s,OLD_STATE,NEW_STATE,1)
    # Legacy fixed-answer prompt text is deliberately left unreachable. A future
    # write-capable managed plugin must replace and review the prompt separately.
    for marker in ('ctx.taskIds && !ctx.taskIds.includes(dialog.task_id)',
                   'if (ctx.dryRun)', 'session.prompt(', 'inflight_reconciled_ack_confirmed',
                   'const dryRun = true;', 'const taskIds = null;',str(CLI)):
        need(marker in s,'EXPECTED_DRYRUN_GUARD_ABSENT')
    need(OLD_CLI not in s and 'bridge_pg.cli' not in s,'BROAD_LEGACY_CLI_SURVIVED')
    return s

def pin():
    need(os.geteuid()==os.getuid() and os.geteuid()!=0 and Path.home()==Path('/home/kali') and
         Path.cwd().resolve()==ROOT,'WRONG_OS_CONTEXT')
    for p,digest in ((SRC,CURRENT_SHA),(CONFIG,CONFIG_SHA)):
        need(trusted(p) and sha(p.read_bytes())==digest,'LIVE_SOURCE_OR_CONFIG_CHANGED')
    need(trusted(CLI),'CLI_UNTRUSTED')
    d=json.loads(CONFIG.read_bytes());e=d.get('plugin')
    need(isinstance(e,list) and len(e)==7,'PLUGIN_COUNT_CHANGED')
    names=[x if isinstance(x,str) else x[0] for x in e]
    need(names.count(str(SRC))==1 and str(DST) not in names and
         not any('mt-git-inbox' in x or 'mt-pg-inbox-canary' in x for x in names),
         'PLUGIN_RUNTIME_CHANGED')
    active=e[names.index(str(SRC))]
    need(isinstance(active,list) and active[1].get('dry_run') is False and
         active[1].get('task_ids')==[CANARY],'CANARY_FILTER_CHANGED')

def main():
    pin()
    need(not DST.exists() and not DST.is_symlink(),'CANDIDATE_ALREADY_EXISTS')
    old=SRC.read_text('utf-8');new=render(old)
    need(sha(SRC.read_bytes())==CURRENT_SHA and sha(CONFIG.read_bytes())==CONFIG_SHA,
         'INPUT_CHANGED_DURING_RENDER')
    fd=os.open(DST,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f:
        f.write(new);f.flush();os.fsync(f.fileno())
    print('STAGE34C_UNREGISTERED_MANAGED_PLUGIN_CANDIDATE_CREATED')
    print('STAGE34C_CANDIDATE_SHA256='+sha(DST.read_bytes()))
    print('STAGE34C_INERT_DRYRUN_NO_SDK_NO_CLAIM_NO_ACK')
    print('STAGE34C_LIVE_CONFIG_AND_PLUGIN_PRESERVED')

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('STAGE34C_STOP_'+(str(e) if isinstance(e,Stop) else type(e).__name__))
        raise SystemExit(2)
