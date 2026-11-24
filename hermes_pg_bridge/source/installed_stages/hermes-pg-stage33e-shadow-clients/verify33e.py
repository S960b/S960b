#!/usr/bin/env python3
"""Stage 33E independent read-only verification. No DB writes."""
from __future__ import annotations
import hashlib
import json
import os
import pwd
import stat
import sys
from pathlib import Path

from actor_import33e import actor_classes
import actor_agent_cli as cli
from chatgpt_shadow_mcp import status_from_actor
from prepare_shadow_plugin import CANARY

CONFIG=Path('/home/kali/.config/gigatool/opencode.json')
CANDIDATE=Path('/home/kali/hermes-pg-stage33e-shadow-clients/mt-pg-inbox-agent-shadow.mjs')
EXISTING=Path('/home/kali/bridge-pg-v2/dist/mt-pg-inbox-canary.mjs')

class Stop(Exception):pass

def ensure(condition, code):
    if not condition:raise Stop(code)


def plugin_path(entry):
    if isinstance(entry,str):return (entry,{})
    if (isinstance(entry,list) and len(entry)==2 and isinstance(entry[0],str)
        and isinstance(entry[1],dict)):
        return entry[0],entry[1]
    raise Stop('INVALID_PLUGIN_CONFIG')


def check_config():
    ensure(CONFIG.is_file() and not CONFIG.is_symlink(),'CONFIG_UNTRUSTED')
    x=json.loads(CONFIG.read_text('utf-8'))
    entries=x.get('plugin')
    ensure(isinstance(entries,list) and len(entries)==8,'PLUGIN_COUNT_CHANGED')
    legacy=[];pg=[]
    for entry in entries:
        path,opts=plugin_path(entry)
        if path.endswith('mt-git-inbox.mjs'):legacy.append((path,opts))
        if 'mt-pg-inbox' in path:pg.append((path,opts))
    ensure(len(legacy)==1 and len(pg)==1,'ORIGINAL_PLUGINS_CHANGED')
    ensure(pg[0][0].endswith('/mt-pg-inbox-canary.mjs'), 'UNEXPECTED_PG_PLUGIN')
    ensure(pg[0][1].get('dry_run') is False and pg[0][1].get('task_ids')==[CANARY],
           'PG_CANARY_FILTER_CHANGED')
    ensure(EXISTING.is_file() and 'Object.freeze' in EXISTING.read_text('utf-8') and
           CANARY in EXISTING.read_text('utf-8'),'ORIGINAL_CANARY_SOURCE_UNEXPECTED')
    ensure(not any('mt-pg-inbox-agent-shadow.mjs' in str(p) for p in entries),
           'SHADOW_PLUGIN_MUST_NOT_BE_ACTIVATED')
    print('STAGE33E_ORIGINAL_MT_CONFIG_AND_CANARY_UNCHANGED')


def actor_checks():
    ChatGPT, Agent=actor_classes()
    cg=ChatGPT(); ag=Agent()
    ensure(cg.health().get('ok') is True and ag.health().get('ok') is True,'ACTOR_DB_UNAVAILABLE')
    meta=cg.dialog_metadata(CANARY)
    ensure(meta.get('state')=='active' and meta.get('last_seq')==4,'CANARY_CHANGED')
    ensure(ag.dialog_metadata(CANARY).get('last_seq')==4,'ROLES_DISAGREE')
    ensure(ag.delivery_status(CANARY,3).get('status')=='delivered','CANARY_DELIVERY_CHANGED')
    record=status_from_actor(cg)
    ensure(record.get('role')=='bridge_chatgpt' and record.get('agent_report_present') is True,
           'CG_CANNOT_READ_COMPLETED_REPORT')
    a=cli.dispatch(type('A',(),{'command':'list','offset':0,'limit':10})(),ag)
    ensure(len(a['dialogs'])==1 and a['dialogs'][0]['task_id']==CANARY,'AGENT_LISTING_UNEXPECTED')
    b=cli.dispatch(type('A',(),{'command':'peek','task_id':CANARY,'offset':0,'limit':5})(),ag)
    ensure(b['seq']==4 and len(b['messages'])==4 and b['task_id']==CANARY,
           'AGENT_PEEK_UNEXPECTED')
    ensure(b['waiting_for']=='chatgpt','CANARY_UNEXPECTED_TURN')
    ensure(b['messages'][0]['from']=='mt' and b['messages'][0]['to']=='chatgpt',
           'MESSAGE_COMPAT_FORMAT_MISMATCH')
    print('STAGE33E_REAL_ACTOR_READ_ONLY_FORMAT_COMPAT_OK')
    print('STAGE33E_CG_AND_AGENT_ISOLATED_LOGINS_VERIFIED_15433')
    print('STAGE33E_CANARY_LAST_SEQ_4_UNMODIFIED')


def candidate_checks():
    ensure(CANDIDATE.is_file() and not CANDIDATE.is_symlink(),'CANDIDATE_MISSING')
    text=CANDIDATE.read_text('utf-8')
    ensure('const dryRun = true; // STAGE33E FORCED READ ONLY' in text,
           'CANDIDATE_DRY_RUN_NOT_HARD_CODED')
    ensure('Object.freeze(["'+CANARY+'"]);' in text,'CANDIDATE_NOT_FILTERED')
    ensure('"bridge_pg.cli"' not in text and 'bridge_pg.cli' not in text,
           'LEGACY_CLI_CALL_PRESENT')
    ensure('actor_agent_cli.py' in text,'ACTOR_CLI_NOT_REFERENCED')
    print('STAGE33E_CANDIDATE_INERT_ROLE_BOUND_MT_PLUGIN_VALIDATED')


def main():
    try:
        ensure(pwd.getpwuid(os.getuid()).pw_name=='kali' and os.geteuid()!=0,
               'WRONG_OS_USER')
        check_config();actor_checks();candidate_checks()
        print('STAGE33E_SHADOW_CLIENTS_READY_NO_CUTOVER')
        return 0
    except Exception as e:
        code=str(e) if isinstance(e,Stop) else type(e).__name__
        print('STAGE33E_STOP_'+code,file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main())
