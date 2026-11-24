#!/usr/bin/env python3
"""37B2B: offline, pinned, read-only crash-window analysis; no client/SDK/SQL calls.

The script deliberately does not interpret/anonymize live logs. It emits only
source-code structure markers from a SHA256-pinned installed candidate.
"""
from __future__ import annotations
import hashlib, os, pwd, re, stat, sys
from pathlib import Path

HOME=Path('/home/kali')
PLUGIN=HOME/'hermes-pg-stage34eb-pilot-candidate/mt-pg-inbox-managed-stage34eb-pilot-unregistered.mjs'
PIN='b3679086583d77c96b6ed1c3dc7b1ed1656707dabba4d3ac74843fe64177d730'
PERMIT=HOME/'.config/bridge-pg-v2/managed-mcp-write-permit35c.json'
GATE=HOME/'.config/bridge-pg-v2/managed-writes-stage34d-armed'
CONFIG=HOME/'.config/gigatool/opencode.json'
CANARY='task_f2c64cfdf7894537b35a19a6def977c6'
E2E='task_b6c26aa18220412bafdbf32393f16568'

class Stop(Exception):pass

def need(x, marker):
    if not x: raise Stop('STAGE37B2B_STOP_'+marker)

def read_trusted(path,limit=250_000):
    st=path.lstat()
    need(stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid()
         and st.st_nlink==1 and (st.st_mode & 0o022)==0
         and 0<st.st_size<=limit,'UNTRUSTED_SOURCE')
    fd=os.open(path, os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        st2=os.fstat(fd)
        need((st.st_ino,st.st_dev,st.st_size)==(st2.st_ino,st2.st_dev,st2.st_size),'SOURCE_RACE')
        b=os.read(fd,limit+1)
        need(len(b)==st.st_size,'SOURCE_CHANGED')
        return b
    finally: os.close(fd)

def code_segment(text,start,end):
    need(text.count(start)==1,'SOURCE_DISPATCH_ANCHOR_COUNT')
    a=text.index(start)
    need(text.count(end)==1,'SOURCE_RECONCILE_ANCHOR_COUNT')
    b=text.index(end)
    need(0<a<b and b-a<17_000,'SOURCE_DISPATCH_BOUNDS')
    return text[a:b]

def inspect(text):
    dispatch=code_segment(text,'async function dispatch(ctx, dialog, message, state) {',
                          'async function reconcileAnsweredInflight(ctx, state, dialog) {')
    # Report only presence/order; never emit source lines, embedded messages or secrets.
    anchors={
      'CLAIM_CALL':'op:"claim"',
      'INFLIGHT_ASSIGN':'state.inflight[key]',
      'STATE_SAVE':'saveState(ctx.paths, state)',
      'SDK_PROMPT':'session.prompt(',
      'ACK_CALL':'op:"ack"',
    }
    pos={}
    for name,needle in anchors.items():
        matches=[m.start() for m in re.finditer(re.escape(needle),dispatch)]
        pos[name]=matches
    result={
        'dispatch':dispatch,'positions':pos,
        'claim_unique':len(pos['CLAIM_CALL'])==1,
        'inflight_present':len(pos['INFLIGHT_ASSIGN'])>=1,
        'save_present':len(pos['STATE_SAVE'])>=1,
        'sdk_unique':len(pos['SDK_PROMPT'])==1,
        'ack_present':len(pos['ACK_CALL'])>=1,
    }
    if all((result['claim_unique'],result['inflight_present'],result['save_present'],result['sdk_unique'])):
        claim=pos['CLAIM_CALL'][0]
        inflight=pos['INFLIGHT_ASSIGN'][0]
        sdk=pos['SDK_PROMPT'][0]
        saves=pos['STATE_SAVE']
        result['claim_before_first_inflight']=claim<inflight
        result['save_after_claim_before_sdk']=any(claim<s<sdk for s in saves)
        result['inflight_before_sdk']=inflight<sdk
        result['risk_claim_without_durable_inflight']=(claim<inflight and any(claim<s<sdk for s in saves))
    else:
        result['risk_claim_without_durable_inflight']=None
    # Atomic persistence audit is intentionally conservative.
    if 'function saveState(' in text:
        save_start=text.index('function saveState(')
        save_end=text.find('\nfunction ',save_start+len('function saveState('))
        save_body=text[save_start:save_end if save_end>0 else save_start+3500]
        result['save_uses_fsync']=('fsyncSync(' in save_body or '.sync()' in save_body or 'fsync(' in save_body)
        result['save_uses_rename']=('renameSync(' in save_body or 'rename(' in save_body)
        result['save_direct_write']=('writeFileSync(' in save_body)
    else:
        result['save_uses_fsync']=None
        result['save_uses_rename']=None
        result['save_direct_write']=None
    return result

def config_check():
    import json
    raw=read_trusted(CONFIG,100_000)
    conf=json.loads(raw)
    entries=conf.get('plugin')
    need(isinstance(entries,list) and len(entries)==8,'CONFIG_NOT_EIGHT_PLUGINS')
    names=[e if isinstance(e,str) else e[0] if isinstance(e,list) and len(e)==2 else None for e in entries]
    need(names.count(str(PLUGIN))==1,'TARGET_NOT_REGISTERED_ONCE')
    ix=names.index(str(PLUGIN))
    item=entries[ix]
    need(isinstance(item,list) and isinstance(item[1],dict) and
         item[1].get('task_ids')==[E2E] and item[1].get('dry_run') is False,
         'TARGET_PLUGIN_POLICY_CHANGED')
    need(any('stage33i' in (n or '') and 'inbox' in (n or '') for n in names),'CANARY_PLUGIN_MISSING')
    return hashlib.sha256(raw).hexdigest()

def main():
    need(len(sys.argv)==2 and sys.argv[1]=='audit','USAGE')
    need(os.getuid()==os.geteuid()!=0 and pwd.getpwuid(os.getuid()).pw_name=='kali' and
         Path.home()==HOME,'WRONG_USER')
    need(not PERMIT.exists() and not PERMIT.is_symlink() and
         not GATE.exists() and not GATE.is_symlink(),'WRITE_CAPABILITY_PRESENT')
    config_sha=config_check()
    source=read_trusted(PLUGIN)
    need(hashlib.sha256(source).hexdigest()==PIN,'PLUGIN_SHA_CHANGED')
    text=source.decode('utf8')
    result=inspect(text)
    print('STAGE37B2B_PINNED_LIVE_PLUGIN_SHA_'+PIN[:12])
    print('STAGE37B2B_CONFIG_EIGHT_PLUGIN_SHA_'+config_sha[:12])
    print('STAGE37B2B_NO_WRITE_CAPABILITIES')
    for tag in ('claim_unique','inflight_present','save_present','sdk_unique','ack_present',
                'claim_before_first_inflight','save_after_claim_before_sdk','inflight_before_sdk',
                'save_uses_fsync','save_uses_rename','save_direct_write'):
        print('STAGE37B2B_SOURCE_'+tag.upper(),str(result.get(tag)).upper())
    if result['risk_claim_without_durable_inflight'] is True:
        print('STAGE37B2B_RISK_CONFIRMED_CLAIM_BEFORE_LOCAL_INFLIGHT_COMMIT')
    elif result['risk_claim_without_durable_inflight'] is False:
        print('STAGE37B2B_SOURCE_SEQUENCE_NOT_AS_ASSUMED_REVIEW_REQUIRED')
    else:
        print('STAGE37B2B_SOURCE_SHAPE_UNRECOGNIZED_REVIEW_REQUIRED')
    print('STAGE37B2B_OFFLINE_INSPECTION_ONLY_NO_SDK_DB_WRITE_OR_CONFIG_CHANGE')
    return 0

if __name__=='__main__':
    try: raise SystemExit(main())
    except Exception as exc:
        print(str(exc) if isinstance(exc,Stop) else 'STAGE37B2B_STOP_'+type(exc).__name__,file=sys.stderr)
        raise SystemExit(2)
