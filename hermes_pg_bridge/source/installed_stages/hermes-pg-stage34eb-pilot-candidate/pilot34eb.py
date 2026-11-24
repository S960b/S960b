#!/usr/bin/env python3
'''Stage 34E-B: prepare a ONE-PILOT managed plugin, unregistered and unarmed.'''
from __future__ import annotations
import hashlib, json, os, stat, sys
from pathlib import Path
ROOT=Path('/home/kali/hermes-pg-stage34eb-pilot-candidate')
SRC=Path('/home/kali/hermes-pg-stage34e-managed-runtime/mt-pg-inbox-managed-stage34e-shadow.mjs')
DST=ROOT/'mt-pg-inbox-managed-stage34eb-pilot-unregistered.mjs'
CONFIG=Path('/home/kali/.config/gigatool/opencode.json')
ACTIVE=Path('/home/kali/hermes-pg-stage33i-ackfix/mt-pg-inbox-agent-stage33i.mjs')
CLI=Path('/home/kali/hermes-pg-stage34d-write-adapter/managed_agent_cli34d.py')
ARM=Path('/home/kali/.config/bridge-pg-v2/managed-writes-stage34d-armed')
SOURCE_SHA='1580178d508174af1edff784b87acd11d339b1d306a057f6112786ffb36122e8'
CONFIG_SHA='e5320f6e971073d5d2c43a64ce85d165cb49dc9a94e6cf7806009ebc2b59ded1'
ACTIVE_SHA='220da977562f9c0763d6b5d5640f686f54f041d357ed397c8482c0d4363f525c'
CLI_SHA='bf8cad69a02e71a70ec3377504c24a3fc2cb71a38f2c8c4a025abc587b774485'
CANARY='task_f2c64cfdf7894537b35a19a6def977c6'
OLD_DRY='const dryRun = true; // STAGE34E SHADOW ONLY; cannot activate SDK through options'
NEW_DRY='const dryRun = false; // STAGE34EB UNREGISTERED PILOT ONLY; WRITE GATE ABSENT'
OLD_TASKIDS='const taskIds = null; // STAGE34C ONLY ag_dispatch_list, forced dry-run'
TICK_ANCHOR='    for (const dialog of dialogs) {'
NEW_FILTER='if (!ctx.taskIds || !ctx.taskIds.includes(dialog?.task_id)) continue;'
PILOT_FN='''function stage34ebPilotTaskIds(options) {
  const ids = options && options.task_ids;
  // Fail closed unless EXACTLY ONE explicit managed ID is supplied.
  if (!Array.isArray(ids) || ids.length !== 1 ||
      typeof ids[0] !== "string" ||
      !/^task_[0-9a-f]{32}$/.test(ids[0]) ||
      ids[0] === "task_f2c64cfdf7894537b35a19a6def977c6")
    return Object.freeze([]);
  return Object.freeze([ids[0]]);
}
'''
NEW_TASKIDS=PILOT_FN+'const taskIds = stage34ebPilotTaskIds(options);'
class Refused(Exception):pass
def need(ok,code):
    if not ok:raise Refused(code)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def trusted(p):
    try:
        s=p.lstat()
        return stat.S_ISREG(s.st_mode) and s.st_nlink==1 and s.st_uid==os.geteuid() and not p.is_symlink()
    except OSError:return False
def pin():
    need(os.getuid()==os.geteuid()!=0 and Path.home()==Path('/home/kali') and Path.cwd().resolve()==ROOT,'wrong_user_or_workdir')
    for p,d in ((SRC,SOURCE_SHA),(CONFIG,CONFIG_SHA),(ACTIVE,ACTIVE_SHA),(CLI,CLI_SHA)):
        need(trusted(p) and sha(p)==d,'pinned_input_changed_'+p.name)
    need(not ARM.exists() and not ARM.is_symlink(),'managed_write_gate_already_exists')
    config=json.loads(CONFIG.read_bytes())
    plugins=config.get('plugin')
    need(isinstance(plugins,list) and len(plugins)==7,'active_plugin_count_changed')
    names=[x if isinstance(x,str) else x[0] if isinstance(x,list) and len(x)==2 else '' for x in plugins]
    need(names.count(str(ACTIVE))==1 and str(SRC) not in names and str(DST) not in names
         and not any('mt-git-inbox.mjs' in n for n in names),'active_plugin_membership_changed')
    entry=plugins[names.index(str(ACTIVE))]
    need(isinstance(entry,list) and len(entry)==2 and isinstance(entry[1],dict) and
         entry[1].get('dry_run') is False and entry[1].get('task_ids')==[CANARY],'canary_policy_changed')
def exactly(s,a,b):
    need(s.count(a)==1,'source_anchor_count_'+a[:36])
    return s.replace(a,b,1)
def render(source):
    need(source.count(OLD_DRY)==1 and source.count(OLD_TASKIDS)==1 and
         source.count(TICK_ANCHOR)==1,'stage34e_source_layout_changed')
    s=exactly(source,OLD_DRY,NEW_DRY)
    s=exactly(s,OLD_TASKIDS,NEW_TASKIDS)
    s=exactly(s,TICK_ANCHOR,TICK_ANCHOR+'\n      '+NEW_FILTER)
    tick=s.split('export async function runTick(ctx) {',1)
    need(len(tick)==2 and NEW_FILTER in tick[1] and
         ('taskIds: taskIds && taskIds.length ? taskIds : null' in s or
          'taskIds: taskIds,' in s or 'taskIds, dryRun' in s) and
         'for (const taskId of Object.keys(state.inflight).sort())' in tick[1] and
         'const dialogs = await listAll(ctx.cliCall)' in tick[1],
         'missing_tick_safety_guards')
    need(tick[1].index('for (const taskId of Object.keys(state.inflight).sort())') <
         tick[1].index('const dialogs = await listAll(ctx.cliCall)'),
         'reconcile_must_precede_discovery')
    required=('managed_agent_cli34d.py','stage34ebPilotTaskIds','ag_dispatch_list',
      'const dryRun = false;',NEW_FILTER,'managedReportId(',
      'op:"ack", task_id:key, seq:message.seq','confirmedMtResponseAfter',
      'managed_report_not_confirmed','managed_inflight_ack_confirmed',
      'const STATE_DIR = join(HOME, ".local/state/mt-pg-inbox-managed-stage34e");')
    for x in required:need(x in s,'safety_marker_missing_'+x[:32])
    for x in ('bridge_pg.cli','inflight_cleared_deleted','PGV2_ROLE_BOUND_MT_SDK_ACK_20261122',
              'mt-git-inbox.mjs','const taskIds = null;'):
        need(x not in s,'legacy_marker_survived_'+x[:30])
    need(s.count('function stage34ebPilotTaskIds(')==1 and
         s.count('const dryRun = false;')==1 and
         s.count('export async function runTick(')==1,'duplicate_source_definitions')
    return s
def verify(before=False):
    pin()
    if before:
        need(not DST.exists() and not DST.is_symlink(),'candidate_already_exists')
        print('STAGE34EB_READ_ONLY_PREFLIGHT_PINS_OK')
        return
    need(trusted(DST),'candidate_missing_or_untrusted')
    need(DST.read_text('utf8')==render(SRC.read_text('utf8')),'candidate_not_byte_exact')
    need(sha(CONFIG)==CONFIG_SHA and sha(SRC)==SOURCE_SHA,'source_or_config_changed')
    print('STAGE34EB_EXACT_ONE_MANAGED_ID_FILTERED')
    print('STAGE34EB_CANARY_EXCLUDED_AND_EMPTY_FILTER_FAIL_CLOSED')
    print('STAGE34EB_ALL_INFLIGHT_RECONCILE_PRESERVED')
    print('STAGE34EB_CANDIDATE_SHA256='+sha(DST))
    print('STAGE34EB_LIVE_CONFIG_AND_WRITE_GATE_UNCHANGED')
def prepare():
    verify(before=True)
    code=render(SRC.read_text('utf8'))
    need(sha(SRC)==SOURCE_SHA and sha(CONFIG)==CONFIG_SHA,'input_changed_during_render')
    fd=os.open(DST,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w',encoding='utf8') as f:
        f.write(code);f.flush();os.fsync(f.fileno())
    print('STAGE34EB_LIVE_CAPABLE_UNREGISTERED_CANDIDATE_CREATED')
    print('STAGE34EB_NO_CONFIG_NO_DB_NO_SDK_MUTATION')
def main():
    try:
        need(len(sys.argv)==2 and sys.argv[1] in ('preflight','prepare','verify'),'invalid_mode')
        if sys.argv[1]=='prepare':prepare()
        else:verify(before=sys.argv[1]=='preflight')
        return 0
    except Exception as e:
        m=str(e) if isinstance(e,Refused) else type(e).__name__
        print('STAGE34EB_STOP_'+m[:100],file=sys.stderr)
        return 2
if __name__=='__main__':raise SystemExit(main())
