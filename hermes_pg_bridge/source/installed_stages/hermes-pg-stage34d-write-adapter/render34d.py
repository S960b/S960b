#!/usr/bin/env python3
"""Build a separate UNREGISTERED, FORCED DRY-RUN managed candidate.

It deliberately keeps Stage34C's fixed-canary prompt until that source is
reviewed and replaced; activating SDK with this candidate is prohibited.
"""
from __future__ import annotations
import hashlib
import json
import os
import stat
from pathlib import Path
ROOT=Path('/home/kali/hermes-pg-stage34d-write-adapter')
SOURCE=Path('/home/kali/hermes-pg-stage34c-managed-inbox-v2/mt-pg-inbox-managed-stage34c.mjs')
DEST=ROOT/'mt-pg-inbox-managed-stage34d-shadow.mjs'
CLI=ROOT/'managed_agent_cli34d.py'
CONFIG=Path('/home/kali/.config/gigatool/opencode.json')
ACTIVE=Path('/home/kali/hermes-pg-stage33i-ackfix/mt-pg-inbox-agent-stage33i.mjs')
ACTIVE_SHA='220da977562f9c0763d6b5d5640f686f54f041d357ed397c8482c0d4363f525c'
CONFIG_SHA='e5320f6e971073d5d2c43a64ce85d165cb49dc9a94e6cf7806009ebc2b59ded1'
SOURCE_SHA='85cdc1102746537e5d315b053a17bd8de011d9d4241bf4c136095ca5f5e1f007'
OLD_CLI='/home/kali/hermes-pg-stage34c-managed-inbox-v2/managed_agent_cli34c.py'
OLD_STATE='const STATE_DIR = join(HOME, ".local/state/mt-pg-inbox-managed-stage34c");'
NEW_STATE='const STATE_DIR = join(HOME, ".local/state/mt-pg-inbox-managed-stage34d");'
OLD_DRY='const dryRun = true; // STAGE34C INERT, options CANNOT ENABLE SDK'
NEW_DRY='const dryRun = true; // STAGE34D SHADOW, SDK PROHIBITED UNTIL SOURCE REWRITE'
CANARY='task_f2c64cfdf7894537b35a19a6def977c6'
class Refused(Exception):pass

def need(v,msg):
    if not v: raise Refused(msg)

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def trusted(p):
    try:
        s=p.lstat();return stat.S_ISREG(s.st_mode) and s.st_nlink==1 and s.st_uid==os.geteuid() and not p.is_symlink()
    except OSError:return False

def verify_inputs():
    need(os.getuid()==os.geteuid()!=0 and Path.home()==Path('/home/kali') and
         Path.cwd().resolve()==ROOT,'wrong_user_or_directory')
    for p,expected in ((SOURCE,SOURCE_SHA),(CONFIG,CONFIG_SHA),(ACTIVE,ACTIVE_SHA)):
        need(trusted(p) and sha(p)==expected,'pinned_source_changed_'+p.name)
    obj=json.loads(CONFIG.read_text())
    plugins=obj.get('plugin')
    need(isinstance(plugins,list) and len(plugins)==7,'active_plugins_changed')
    entries=[e if isinstance(e,str) else e[0] for e in plugins]
    need(entries.count(str(ACTIVE))==1 and str(DEST) not in entries and
         not any('mt-git-inbox' in e for e in entries),'active_plugin_unsafe')
    active=plugins[entries.index(str(ACTIVE))]
    need(isinstance(active,list) and active[1].get('dry_run') is False and
         active[1].get('task_ids')==[CANARY],'canary_guard_changed')

def once(s,a,b,n):
    need(s.count(a)==n,'candidate_source_layout_changed')
    return s.replace(a,b)

def render(src):
    s=once(src,OLD_CLI,str(CLI),2)
    s=once(s,OLD_STATE,NEW_STATE,1)
    s=once(s,OLD_DRY,NEW_DRY,1)
    for marker in ('const dryRun = true;','const taskIds = null;',
                   'ctx.taskIds && !ctx.taskIds.includes(dialog.task_id)',
                   'inflight_reconciled_ack_confirmed','session.prompt('):
        need(marker in s,'guard_missing_'+marker[:20])
    need(OLD_CLI not in s and 'bridge_pg.cli' not in s,'broad_cli_found')
    # THIS candidate remains strictly inert; Stage34E must replace buildPrompt
    # and change reconciliation independently of ag_dispatch_list.
    return s

def excerpts(src):
    lines=src.splitlines()
    anchors=('function buildPrompt(', 'async function dispatch(',
             'async function reconcileAnsweredInflight(',
             'export async function runTick(', 'function confirmedMtResponseAfter(')
    out=[]
    for anchor in anchors:
        matches=[i for i,line in enumerate(lines) if anchor in line]
        need(len(matches)==1,'function_anchor_changed_'+anchor[:20])
        start=matches[0]
        # Conservatively capture relevant function segment for independent review
        nxt=next((j for j in range(start+1,len(lines)) if lines[j]=='}'),None)
        need(nxt is not None and nxt-start<210,'function_section_too_long')
        out.append({'function':anchor,'start_line':start+1,'end_line':nxt+1,
                    'sha256':hashlib.sha256('\n'.join(lines[start:nxt+1]).encode()).hexdigest(),
                    'source':'\n'.join(f'{idx+1}: {lines[idx]}' for idx in range(start,nxt+1))})
    return out

def main():
    verify_inputs()
    need(not DEST.exists() and not DEST.is_symlink(),'candidate_exists')
    src=SOURCE.read_text()
    rendered=render(src)
    fragments=excerpts(src)
    need(sha(SOURCE)==SOURCE_SHA,'source_changed_during_render')
    fd=os.open(DEST,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:
        f.write(rendered); f.flush();os.fsync(f.fileno())
    print('STAGE34D_UNREGISTERED_DRYRUN_CANDIDATE_CREATED')
    print('STAGE34D_JS_SHA256='+sha(DEST))
    print('STAGE34D_NO_ACTIVE_MCP_OR_MT_CONFIG_CHANGE')
    # Store only source code excerpts from pinned JS, no runtime secrets.
    report=ROOT/'plugin_contract34d.json'
    fd=os.open(report,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:
        json.dump({'source_sha256':SOURCE_SHA,'segments':fragments},f,ensure_ascii=False,indent=2)
        f.flush();os.fsync(f.fileno())
    print('STAGE34D_SOURCE_CONTRACT_EXTRACTED_FOR_NEXT_SDK_STAGE')

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('STAGE34D_STOP_'+str(exc)[:90] if isinstance(exc,Refused) else 'STAGE34D_STOP_'+type(exc).__name__)
        raise SystemExit(2)
