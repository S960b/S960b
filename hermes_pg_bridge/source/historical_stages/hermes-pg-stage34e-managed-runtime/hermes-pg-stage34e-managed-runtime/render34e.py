#!/usr/bin/env python3
"""Stage34E: fail-closed pin-based rendering of an UNREGISTERED forced-dry-run plugin.

No SQL writes, SDK calls, systemd changes or live config edits are performed.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import stat
from pathlib import Path

ROOT=Path('/home/kali/hermes-pg-stage34e-managed-runtime')
SRC=Path('/home/kali/hermes-pg-stage34d-write-adapter/mt-pg-inbox-managed-stage34d-shadow.mjs')
DEST=ROOT/'mt-pg-inbox-managed-stage34e-shadow.mjs'
PARTS=ROOT/'parts34e.js'
CONFIG=Path('/home/kali/.config/gigatool/opencode.json')
ACTIVE=Path('/home/kali/hermes-pg-stage33i-ackfix/mt-pg-inbox-agent-stage33i.mjs')
CLI=Path('/home/kali/hermes-pg-stage34d-write-adapter/managed_agent_cli34d.py')
WRITE_ARM=Path('/home/kali/.config/bridge-pg-v2/managed-writes-stage34d-armed')
SOURCE_SHA='ea06a7fb0fc7c3aa26abb5678de380776e634706a6146fe5b9340a33be956c2d'
CONFIG_SHA='e5320f6e971073d5d2c43a64ce85d165cb49dc9a94e6cf7806009ebc2b59ded1'
ACTIVE_SHA='220da977562f9c0763d6b5d5640f686f54f041d357ed397c8482c0d4363f525c'
CLI_SHA='bf8cad69a02e71a70ec3377504c24a3fc2cb71a38f2c8c4a025abc587b774485'
CANARY='task_f2c64cfdf7894537b35a19a6def977c6'
OLD_STATE='const STATE_DIR = join(HOME, ".local/state/mt-pg-inbox-managed-stage34d");'
NEW_STATE='const STATE_DIR = join(HOME, ".local/state/mt-pg-inbox-managed-stage34e");'
OLD_DRY='const dryRun = true; // STAGE34D SHADOW, SDK PROHIBITED UNTIL SOURCE REWRITE'
NEW_DRY='const dryRun = true; // STAGE34E SHADOW ONLY; cannot activate SDK through options'
ANCHORS={
 'CLI':'function cli(request, timeoutMs = CLI_TIMEOUT_MS) {',
 'PROMPT':'function buildPrompt(dialog, message) {',
 'CONFIRMED':'function confirmedMtResponseAfter(snapshot, taskId, reserved) {',
 'RECONCILE':'async function reconcileAnsweredInflight(ctx, state, dialog) {',
 'TICK':'export async function runTick(ctx) {',
 'DIAGNOSTIC':'export function diagnosticContext(',
 'LATEST':'function latestPending(snapshot, seenSeq) {',
}

class Refused(Exception):pass

def need(v,code):
    if not v:raise Refused(code)

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def trusted(p):
    try:
        st=p.lstat()
        return stat.S_ISREG(st.st_mode) and not p.is_symlink() and st.st_nlink==1 and st.st_uid==os.geteuid()
    except OSError:return False

def preflight():
    need(os.getuid()==os.geteuid()!=0 and Path.home()==Path('/home/kali') and Path.cwd().resolve()==ROOT,'wrong_execution_context')
    for path,expected in ((SRC,SOURCE_SHA),(CONFIG,CONFIG_SHA),(ACTIVE,ACTIVE_SHA),(CLI,CLI_SHA)):
        need(trusted(path) and sha(path)==expected,'pinned_input_changed_'+path.name)
    need(trusted(PARTS),'parts_file_untrusted')
    need(not WRITE_ARM.exists() and not WRITE_ARM.is_symlink(),'managed_write_gate_present')
    obj=json.loads(CONFIG.read_text('utf-8'))
    plugins=obj.get('plugin')
    need(isinstance(plugins,list) and len(plugins)==7,'active_plugin_count_changed')
    names=[x if isinstance(x,str) else x[0] if isinstance(x,list) and x else '' for x in plugins]
    need(names.count(str(ACTIVE))==1 and str(SRC) not in names and str(DEST) not in names and
         not any('mt-git-inbox.mjs' in n for n in names),'active_plugin_membership_changed')
    entry=plugins[names.index(str(ACTIVE))]
    need(isinstance(entry,list) and len(entry)==2 and isinstance(entry[1],dict) and
         entry[1].get('dry_run') is False and entry[1].get('task_ids')==[CANARY],
         'canary_plugin_policy_changed')

def parts(source):
    result={}
    for name in ('CLI','PROMPT','CONFIRMED','RECONCILE','TICK'):
        begin='// BEGIN STAGE34E '+name+'\n'
        end='\n// END STAGE34E '+name
        need(source.count(begin)==1 and source.count(end)==1,'parts_section_missing_'+name)
        s=source.split(begin,1)[1].split(end,1)[0]
        need(len(s)>100 and '\x00' not in s,'parts_section_bad_'+name)
        result[name]=s.rstrip()+'\n'
    return result

def between(text,start,end,replacement,label):
    need(text.count(start)==1 and text.count(end)==1,'source_anchor_count_'+label)
    a=text.index(start)
    b=text.index(end,a+len(start))
    need(a<b and b-a<17000,'source_anchor_order_'+label)
    return text[:a]+replacement+'\n'+text[b:]

def patch_dispatch_ack(s):
    start=ANCHORS['RECONCILE']
    begin='async function dispatch(ctx, dialog, message, state) {'
    need(s.count(begin)==1 and s.count(start)==1,'dispatch_bounds_changed')
    a=s.index(begin);b=s.index(start,a+len(begin))
    section=s[a:b]
    pat=r'const\s+acked\s*=\s*await\s+ctx\.cliCall\(\{op:"ack",\s*delivery_id:lease\.delivery_id,\s*lease_token:lease\.lease_token\}\)\s*;'
    matches=list(re.finditer(pat,section))
    need(len(matches)==1,'dispatch_ack_shape_changed')
    need(section.count('op:"ack"')==1,'dispatch_duplicate_ack')
    # ACK only after the exact deterministic managed report has committed.
    # A successful SDK prompt by itself does not prove a durable report.
    proof = ('const managedCompletion = await ctx.cliCall({op:"view", task_id:key, offset:0, limit:2});\n'
             '  if (!confirmedMtResponseAfter(managedCompletion, key,\n'
             '      {seq:message.seq, message_id:message.message_id}))\n'
             '    throw new Error("managed_report_not_confirmed");\n'
             '  ')
    ack_call=('const acked = await ctx.cliCall({op:"ack", task_id:key, seq:message.seq,\n'
              '                                  delivery_id:lease.delivery_id,\n'
              '                                  lease_token:lease.lease_token});')
    section=re.sub(pat,lambda _:proof+ack_call,section,count=1)
    return s[:a]+section+s[b:]

def render(source, snippets):
    need(source.count(OLD_STATE)==1 and source.count(OLD_DRY)==1,'old_dryrun_or_state_changed')
    need('const taskIds = null;' in source and
         'inflight_reconciled_ack_confirmed' in source and
         'session.prompt(' in source,'previous_safety_pins_absent')
    s=source.replace(OLD_STATE,NEW_STATE,1).replace(OLD_DRY,NEW_DRY,1)
    s=between(s,ANCHORS['CLI'],ANCHORS['PROMPT'],snippets['CLI'],'cli')
    s=between(s,ANCHORS['PROMPT'],ANCHORS['LATEST'],snippets['PROMPT'],'prompt')
    s=between(s,ANCHORS['CONFIRMED'],ANCHORS['RECONCILE'],snippets['CONFIRMED'],'confirmed')
    s=between(s,ANCHORS['RECONCILE'],ANCHORS['TICK'],snippets['RECONCILE'],'reconcile')
    s=between(s,ANCHORS['TICK'],ANCHORS['DIAGNOSTIC'],snippets['TICK'],'tick')
    s=patch_dispatch_ack(s)
    required=(
      NEW_DRY,NEW_STATE,'const taskIds = null;',
      'async function reconcileAnsweredInflight(',
      'for (const taskId of Object.keys(state.inflight).sort())',
      'managedReportId(', 'managed_agent_cli34d.py',
      '"--consumer", "mt-pg-managed"',
      'op:"ack", task_id:key, seq:message.seq',
      'state.inflight[key]', 'session.prompt(', 'ag_dispatch_list',
    )
    for marker in required:need(marker in s,'patched_guard_missing_'+marker[:30])
    forbidden=('PGV2_ROLE_BOUND_MT_SDK_ACK_20261122','pgv2-stage33f-sdk-report-v1',
               '/home/kali/hermes-pg-stage34c-managed-inbox-v2/managed_agent_cli34c.py',
               'bridge_pg.cli','inflight_cleared_deleted',
               'const dryRun = false;')
    for marker in forbidden:need(marker not in s,'legacy_unsafe_text_survived_'+marker[:30])
    need(s.count('function cli(')==1 and s.count('function buildPrompt(')==1 and
         s.count('export async function runTick(')==1,'function_count_changed')
    return s

def output(path,text):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f:
        f.write(text);f.flush();os.fsync(f.fileno())

def main():
    preflight()
    need(not DEST.exists() and not DEST.is_symlink(),'candidate_already_exists')
    original=SRC.read_text('utf-8')
    rendered=render(original,parts(PARTS.read_text('utf-8')))
    # Bail out on any concurrent changes, before writing output.
    need(sha(SRC)==SOURCE_SHA and sha(CONFIG)==CONFIG_SHA,'source_changed_during_render')
    output(DEST,rendered)
    print('STAGE34E_UNREGISTERED_FORCED_DRYRUN_CANDIDATE_CREATED')
    print('STAGE34E_CANDIDATE_SHA256='+sha(DEST))
    print('STAGE34E_MANAGED_PROMPT_AND_IDENTITY_BOUND_ACK_INSTALLED_IN_CANDIDATE')
    print('STAGE34E_ALL_INFLIGHT_RECONCILIATION_ADDED')
    print('STAGE34E_NO_ACTIVE_PLUGIN_OR_DB_CHANGE')

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('STAGE34E_STOP_'+(str(e) if isinstance(e,Refused) else type(e).__name__)[:90])
        raise SystemExit(2)
