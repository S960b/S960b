#!/usr/bin/env python3
"""Render an UNREGISTERED, bounded Stage 33I MT plugin and actor CLI.

Never edits running plugin, opencode.json, database, systemd or SDK state.
Refuses every unforeseen source/config change.
"""
from __future__ import annotations
import hashlib
import json
import os
import stat
from pathlib import Path

ROOT = Path('/home/kali/hermes-pg-stage33i-ackfix')
ORIGINAL_PLUGIN = Path('/home/kali/hermes-pg-stage33f-sdk-preflight/mt-pg-inbox-agent-stage33f.mjs')
ORIGINAL_CLI = Path('/home/kali/hermes-pg-stage33f-sdk-preflight/stage33f_agent_cli.py')
NEW_PLUGIN = ROOT / 'mt-pg-inbox-agent-stage33i.mjs'
NEW_CLI = ROOT / 'stage33i_agent_cli.py'
CONFIG = Path('/home/kali/.config/gigatool/opencode.json')
CANARY = 'task_f2c64cfdf7894537b35a19a6def977c6'
EXPECTED_PLUGIN_SHA = '3535ae7bd5bb0da3e8574afcab299118dac744bc851c4bcb90b8656acd8b3ea7'
EXPECTED_CLI_SHA = '13f098ecd5367345880c9616feaa2d36ca3f6a9d1f4b93ec03dd7529200ed4cc'
EXPECTED_CONFIG_SHA = 'a8f11a25173b5e7f1290d97b4aedfaea24403256fa09c16144093e0f9d297cb3'

OLD_CLI_PATH = str(ORIGINAL_CLI)
NEW_CLI_PATH = str(NEW_CLI)

OLD_RECONCILE_TAIL = '''  state.seen[taskId] = Math.max(state.seen[taskId] || 0, reserved.seq);
  delete state.inflight[taskId];
  saveState(ctx.paths, state);
  ctx.log("inflight_reconciled_confirmed_response " + taskId +
          " seq=" + reserved.seq);
  return true;
}'''

NEW_RECONCILE_TAIL = '''  // A confirmed response proves SDK completion, NOT outbox acknowledgement.
  // Keep the durable reservation until PostgreSQL has confirmed this lease.
  // Missing lease information is an ambiguous delivery: NEVER replay SDK.
  if (!Number.isSafeInteger(reserved.delivery_id) || reserved.delivery_id <= 0 ||
      typeof reserved.lease_token !== "string" ||
      !/^[0-9a-fA-F-]{36}$/.test(reserved.lease_token)) {
    ctx.log("inflight_reconcile_missing_lease_receipt " + taskId);
    return false;
  }
  let acked;
  try {
    acked = await ctx.cliCall({op:"ack", delivery_id:reserved.delivery_id,
                               lease_token:reserved.lease_token});
  } catch {
    // Including a lost response after a DB-committed ACK: the actor CLI
    // treats already-delivered status for the SAME delivery_id as success.
    ctx.log("inflight_reconcile_ack_outcome_unconfirmed " + taskId);
    return false;
  }
  if (acked?.acked !== true) {
    ctx.log("inflight_reconcile_ack_not_confirmed " + taskId);
    return false;
  }
  // Only after positive, identity-bound DB receipt may the SDK reservation
  // be cleared. This path NEVER invokes session.prompt or dispatch.
  state.seen[taskId] = Math.max(state.seen[taskId] || 0, reserved.seq);
  delete state.inflight[taskId];
  saveState(ctx.paths, state);
  ctx.log("inflight_reconciled_ack_confirmed " + taskId +
          " seq=" + reserved.seq);
  return true;
}'''

OLD_CLI_ACK = '''    if op=='ack':
        need(type(args.delivery_id) is int and args.delivery_id>0 and
             isinstance(args.lease_token,str) and
             re.fullmatch(r'[0-9a-fA-F-]{36}',args.lease_token) and
             args.consumer==CONSUMER,'ack_args_denied')
        status=agent.delivery_status(CANARY,5)
        need(isinstance(status,dict) and status.get('delivery_id')==args.delivery_id and
             status.get('status')=='leased','ack_not_bound_to_test_turn')
        return {'acked':agent.ack_outbox(args.delivery_id,args.lease_token,CONSUMER) is True}
'''
NEW_CLI_ACK = '''    if op=='ack':
        need(type(args.delivery_id) is int and args.delivery_id>0 and
             isinstance(args.lease_token,str) and
             re.fullmatch(r'[0-9a-fA-F-]{36}',args.lease_token) and
             args.consumer==CONSUMER,'ack_args_denied')
        status=agent.delivery_status(CANARY,5)
        need(isinstance(status,dict) and status.get('delivery_id')==args.delivery_id and
             status.get('status') in ('leased','delivered'),
             'ack_not_bound_to_test_turn')
        # A lost response after a committed ACK must be safely reconcilable.
        # Only this exact canary delivery_id can be accepted as already done.
        if status['status']=='delivered':
            return {'acked':True,'already_delivered':True}
        try:
            acked=agent.ack_outbox(args.delivery_id,args.lease_token,CONSUMER)
        except Exception:
            acked=False
        if acked is True:
            return {'acked':True,'already_delivered':False}
        # Read-after-uncertain-write. Never claim, release or dispatch again.
        check=agent.delivery_status(CANARY,5)
        need(isinstance(check,dict) and
             check.get('delivery_id')==args.delivery_id and
             check.get('status')=='delivered','ack_unconfirmed')
        return {'acked':True,'already_delivered':True}
'''


def sha(blob:bytes)->str:
    return hashlib.sha256(blob).hexdigest()


def single(src:str, old:str, new:str, label:str)->str:
    if src.count(old)!=1:
        raise RuntimeError('unexpected_'+label+'_source_layout')
    return src.replace(old,new,1)


def patch_js(source:str)->str:
    if source.count(OLD_CLI_PATH)!=2:
        raise RuntimeError('unexpected_cli_path_count')
    begin='async function reconcileAnsweredInflight(ctx, state, dialog) {'
    end='export async function runTick(ctx) {'
    if source.count(begin)!=1 or source.count(end)!=1:
        raise RuntimeError('reconcile_bounds_changed')
    a=source.index(begin); b=source.index(end,a)
    section=source[a:b]
    section=single(section,OLD_RECONCILE_TAIL,NEW_RECONCILE_TAIL,'reconcile_tail')
    patched=source[:a]+section+source[b:]
    patched=patched.replace(OLD_CLI_PATH,NEW_CLI_PATH)
    for required in (
      'Object.freeze(["'+CANARY+'"]);',
      'const dryRun = false; // STAGE33F PROSPECTIVE ONLY: NOT REGISTERED',
      'const STATE_DIR = join(HOME, ".local/state/mt-pg-inbox-agent-stage33f");',
      'session.prompt(',
      'op:"claim"',
      'op:"ack"',
      'state.inflight[key]',
      'PGV2_ROLE_BOUND_MT_SDK_ACK_20261122',
      'inflight_reconciled_ack_confirmed',
    ):
        if required not in patched:
            raise RuntimeError('plugin_guard_absent')
    if OLD_CLI_PATH in patched or 'bridge_pg.cli' in patched:
        # Legacy name may occur in comments; no executable dependency allowed.
        raise RuntimeError('legacy_plugin_dependency_survived')
    return patched


def patch_cli(source:str)->str:
    patched=single(source,OLD_CLI_ACK,NEW_CLI_ACK,'cli_ack')
    for required in ("CANARY='"+CANARY+"'", "TASK_MID='pgv2-stage33f-sdk-task-v1'",
                     "REPORT_MID='pgv2-stage33f-sdk-report-v1'",
                     "ACK='PGV2_ROLE_BOUND_MT_SDK_ACK_20261122'",
                     "need(op in ('list','peek','read','claim','ack','append','status')", 
                     "ack_not_bound_to_test_turn", "already_delivered"):
        if required not in patched:
            raise RuntimeError('cli_guard_absent')
    return patched


def trusted(path:Path)->bool:
    st=path.lstat()
    return stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid() and st.st_nlink==1 and not path.is_symlink()


def preflight():
    if os.geteuid()==0 or Path.home()!=Path('/home/kali') or Path.cwd().resolve()!=ROOT:
        raise RuntimeError('invalid_execution_context')
    for p in (ORIGINAL_PLUGIN,ORIGINAL_CLI,CONFIG):
        if not trusted(p):raise RuntimeError('untrusted_source_'+p.name)
    if sha(ORIGINAL_PLUGIN.read_bytes())!=EXPECTED_PLUGIN_SHA:
        raise RuntimeError('active_plugin_hash_changed')
    if sha(ORIGINAL_CLI.read_bytes())!=EXPECTED_CLI_SHA:
        raise RuntimeError('active_cli_hash_changed')
    if sha(CONFIG.read_bytes())!=EXPECTED_CONFIG_SHA:
        raise RuntimeError('active_config_hash_changed')
    obj=json.loads(CONFIG.read_bytes())
    entries=obj.get('plugin')
    if not isinstance(entries,list) or len(entries)!=7:
        raise RuntimeError('active_plugin_count_changed')
    active=[x for x in entries if isinstance(x,list) and len(x)==2 and x[0]==str(ORIGINAL_PLUGIN)]
    if len(active)!=1 or active[0][1].get('dry_run') is not False or active[0][1].get('task_ids')!=[CANARY]:
        raise RuntimeError('active_plugin_unexpected')
    if any('mt-git-inbox' in str(e) or str(NEW_PLUGIN) in str(e) for e in entries):
        raise RuntimeError('conflicting_plugin_registered')
    for p in (NEW_PLUGIN, NEW_CLI):
        if p.exists() or p.is_symlink():
            raise RuntimeError('candidate_already_exists')


def exclusive_write(path:Path,text:str):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w',encoding='utf8') as f:
        f.write(text); f.flush();os.fsync(f.fileno())


def main():
    preflight()
    original_js=ORIGINAL_PLUGIN.read_text('utf8'); original_py=ORIGINAL_CLI.read_text('utf8')
    new_js=patch_js(original_js)
    new_py=patch_cli(original_py)
    if sha(ORIGINAL_PLUGIN.read_bytes())!=EXPECTED_PLUGIN_SHA or sha(CONFIG.read_bytes())!=EXPECTED_CONFIG_SHA:
        raise RuntimeError('source_changed_during_build')
    exclusive_write(NEW_CLI,new_py)
    try:
        exclusive_write(NEW_PLUGIN,new_js)
    except Exception:
        NEW_CLI.unlink(missing_ok=True)
        raise
    print('STAGE33I_CANDIDATES_CREATED_UNREGISTERED')
    print('STAGE33I_PLUGIN_SHA256='+sha(NEW_PLUGIN.read_bytes()))
    print('STAGE33I_ACTOR_CLI_SHA256='+sha(NEW_CLI.read_bytes()))
    print('STAGE33I_ACTIVE_PLUGIN_AND_CONFIG_UNCHANGED')
    print('STAGE33I_NO_SDK_NO_ACK_NO_DB_MUTATION')

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('STAGE33I_STOP_'+str(e).splitlines()[0][:100])
        raise SystemExit(2)
