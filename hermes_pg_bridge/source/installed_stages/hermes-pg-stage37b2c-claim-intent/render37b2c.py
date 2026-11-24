#!/usr/bin/env python3
"""Render an UNREGISTERED fail-closed candidate from a SHA-pinned live plugin.

NO active config, SQL, SDK, credential, dispatch or permit mutations. The real
source remains unchanged. A semantic mismatch aborts instead of guessing.
"""
from __future__ import annotations
import hashlib, json, os, pwd, re, stat, sys
from pathlib import Path

HOME=Path('/home/kali')
ROOT=HOME/'hermes-pg-stage37b2c-claim-intent'
SRC=HOME/'hermes-pg-stage34eb-pilot-candidate/mt-pg-inbox-managed-stage34eb-pilot-unregistered.mjs'
DEST=ROOT/'mt-pg-inbox-managed-stage37b2c-unregistered.mjs'
HELPER=ROOT/'intent37b2c.mjs'
CONFIG=HOME/'.config/gigatool/opencode.json'
PERMIT=HOME/'.config/bridge-pg-v2/managed-mcp-write-permit35c.json'
AGENT_GATE=HOME/'.config/bridge-pg-v2/managed-writes-stage34d-armed'
SOURCE_SHA='b3679086583d77c96b6ed1c3dc7b1ed1656707dabba4d3ac74843fe64177d730'
TASK='task_b6c26aa18220412bafdbf32393f16568'
RECONCILE='async function reconcileAnsweredInflight(ctx, state, dialog) {'
DISPATCH='async function dispatch(ctx, dialog, message, state) {'
TICK='export async function runTick(ctx) {'
NEXT='export function diagnosticContext('
IMPORT='import { stage37b2cIntentPresent, stage37b2cWriteIntent, stage37b2cClearAfterAck, stage37b2cFinalizeAfterConfirmedAck } from "'+str(HELPER)+'";\n'

class Stop(Exception): pass
def need(ok,why):
    if not ok: raise Stop('STAGE37B2C_STOP_'+why)
def sha(b): return hashlib.sha256(b).hexdigest()
def source(src):
    if isinstance(src,bytes):src=src.decode('utf8')
    need(src.count(DISPATCH)==1 and src.count(RECONCILE)==1 and
         src.count(TICK)==1 and src.count(NEXT)==1,'UNRECOGNIZED_SOURCE_SHAPE')
    a=src.index(DISPATCH); b=src.index(RECONCILE);c=src.index(TICK);d=src.index(NEXT)
    need(a<b<c<d and b-a<18000 and d-c<13000,'INVALID_FUNCTION_BOUNDS')
    return a,b,c,d

def patched(text):
    a,b,c,d=source(text)
    dispatch=text[a:b]
    reconcile=text[b:c]
    tick=text[c:d]
    need(dispatch.count('op:"claim"')==1 and dispatch.count('op:"ack"')==1 and
         dispatch.count('session.prompt(')==1 and
         dispatch.index('op:"claim"')<dispatch.index('state.inflight[key]')<dispatch.index('session.prompt('),
         'CLAIM_OR_SDK_ORDER_CHANGED')
    need('managed_report_not_confirmed' in dispatch and
         dispatch.index('managed_report_not_confirmed') < dispatch.index('op:"ack"'),
         'REPORT_PROOF_NOT_BEFORE_ACK')
    # Strong sanity check: successful ACK drives seen/clear after the ACK operation.
    postack=dispatch[dispatch.index('op:"ack"'):]
    need('state.seen[key]' in postack and 'delete state.inflight[key]' in postack and
         'saveState(ctx.paths, state)' in postack,
         'ACK_STATE_COMMIT_SHAPE_UNRECOGNIZED')
    pattern=r'(?m)^([ \t]*)(?:const|let)\s+lease\s*=\s*await\s+ctx\.cliCall\(\{op:"claim"'
    hits=list(re.finditer(pattern,dispatch))
    need(len(hits)==1,'CLAIM_CALL_ASSIGNMENT_UNRECOGNIZED')
    hit=hits[0];indent=hit.group(1)
    dispatch=dispatch[:hit.start()]+(indent+'stage37b2cWriteIntent(key, message.seq, message.message_id);\n')+dispatch[hit.start():]
    # Reconciliation cleanup is after the verified ACK, seen update, inflight deletion and local state save.
    anchor='  ctx.log("managed_inflight_ack_confirmed " + taskId + " seq=" + reserved.seq);'
    need(reconcile.count(anchor)==1 and 'if (ack?.acked !== true) return false;' in reconcile,
         'RECONCILE_ACK_ANCHOR_CHANGED')
    before=reconcile[:reconcile.index(anchor)]
    need(before.rfind('saveState(ctx.paths, state);')>before.rfind('delete state.inflight[taskId];')
         >before.rfind('if (ack?.acked !== true) return false;'),
         'RECONCILE_COMMIT_ORDER_CHANGED')
    reconcile=reconcile.replace(anchor,
        '  if (stage37b2cIntentPresent(taskId))\n'
        '    stage37b2cFinalizeAfterConfirmedAck(state, taskId, reserved.seq, reserved.message_id);\n'
        +anchor,1)
    # A journal surviving a crash takes precedence over new claims. It is NOT auto-erased on lease TTL.
    guard='      if (state.inflight[dialog.task_id]) continue;'
    need(tick.count(guard)==1,'DISPATCH_GUARD_UNRECOGNIZED')
    tick=tick.replace(guard,guard+'\n'
      '      if (stage37b2cIntentPresent(dialog.task_id)) {\n'
      '        ctx.log("stage37b2c_uncertain_claim_hold " + dialog.task_id);\n'
      '        continue;\n'
      '      }',1)
    original='      try { if (await dispatch(ctx, dialog, full, state)) dispatched++; }'
    need(tick.count(original)==1,'TICK_SUCCESS_ANCHOR_UNRECOGNIZED')
    tick=tick.replace(original,
      '      try {\n'
      '        if (await dispatch(ctx, dialog, full, state)) {\n'
      '          // The original dispatch ACK has already completed and updated durable local state.\n'
      '          stage37b2cFinalizeAfterConfirmedAck(state, dialog.task_id, full.seq, full.message_id);\n'
      '          dispatched++;\n'
      '        }\n'
      '      }',1)
    # Slice from end to start, retaining all other byte-for-byte source.
    result=text[:a]+dispatch+reconcile+tick+text[d:]
    result=IMPORT+result
    source(result)
    need(result.count('stage37b2cWriteIntent(key, message.seq, message.message_id)')==1 and
         result.count('stage37b2cFinalizeAfterConfirmedAck(')==2 and
         result.count('stage37b2c_uncertain_claim_hold')==1 and
         result.count('session.prompt(')==1 and
         result.count('op:"claim"')==1,
         'RENDER_VERIFICATION_FAILED')
    dispatch2=result[result.index(DISPATCH):result.index(RECONCILE)]
    need(dispatch2.index('stage37b2cWriteIntent(')<dispatch2.index('op:"claim"')<
         dispatch2.index('state.inflight[key]')<dispatch2.index('session.prompt('),
         'POSTPATCH_UNSAFE_ORDER')
    return result

def trusted_file(p,limit=260000):
    st=p.lstat()
    need(stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid() and st.st_nlink==1 and
         not p.is_symlink() and (st.st_mode&0o077)==0 and 0<st.st_size<=limit,'UNTRUSTED_FILE_'+p.name)
    fd=os.open(p,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
    try:
        now=os.fstat(fd)
        need((now.st_dev,now.st_ino,now.st_size)==(st.st_dev,st.st_ino,st.st_size),'RACE_'+p.name)
        data=os.read(fd,limit+1)
        need(len(data)==st.st_size,'SHORT_READ_'+p.name)
        return data
    finally:os.close(fd)

def status():
    need(os.getuid()==os.geteuid()!=0 and pwd.getpwuid(os.getuid()).pw_name=='kali' and Path.home()==HOME,
         'UNEXPECTED_OS_USER')
    need(not PERMIT.exists() and not PERMIT.is_symlink() and
         not AGENT_GATE.exists() and not AGENT_GATE.is_symlink(),'WRITE_PERMISSION_ACTIVE')
    config=json.loads(trusted_file(CONFIG,120000))
    plugins=config.get('plugin')
    need(isinstance(plugins,list) and len(plugins)==8,'EXPECTED_EIGHT_PLUGINS')
    names=[p if isinstance(p,str) else p[0] if isinstance(p,list) and len(p)==2 else None for p in plugins]
    need(names.count(str(SRC))==1,'PINNED_PLUGIN_NOT_REGISTERED')
    opts=plugins[names.index(str(SRC))]
    need(isinstance(opts,list) and isinstance(opts[1],dict) and opts[1].get('task_ids')==[TASK]
         and opts[1].get('dry_run') is False,'PLUGIN_OPTIONS_DRIFT')
    need(str(DEST) not in names,'CANDIDATE_ALREADY_REGISTERED')
    sourceblob=trusted_file(SRC)
    need(sha(sourceblob)==SOURCE_SHA,'SOURCE_SHA_MISMATCH')
    need(sha(trusted_file(HELPER))==helper_sha(),'HELPER_CHANGED')
    return sourceblob.decode('utf8'),sha(trusted_file(CONFIG))

def helper_sha():
    # SHA of the installed helper is anchored to the immutable package manifest.
    return Path(ROOT/'helper.sha256').read_text('ascii').strip()

def put_private(path,data):
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:
        f.write(data);f.flush();os.fsync(f.fileno())

def render():
    original,cfg_sha=status()
    need(not DEST.exists() and not DEST.is_symlink(),'CANDIDATE_ALREADY_EXISTS')
    result=patched(original)
    need(sha(trusted_file(SRC))==SOURCE_SHA,'SOURCE_CHANGED_DURING_RENDER')
    put_private(DEST,result.encode('utf8'))
    print('STAGE37B2C_PINNED_SOURCE_'+SOURCE_SHA[:12])
    print('STAGE37B2C_CONFIG_EIGHT_SHA_'+cfg_sha[:12])
    print('STAGE37B2C_CANDIDATE_CREATED_UNREGISTERED')
    print('STAGE37B2C_JOURNAL_FSYNC_BEFORE_CLAIM')
    print('STAGE37B2C_UNCERTAIN_CLAIM_BLOCKS_REDISPATCH')
    print('STAGE37B2C_NORMAL_ACK_CLEARS_INTENT')
    print('STAGE37B2C_NO_LIVE_CONFIG_DB_OR_SERVICE_CHANGE')

def verify():
    original,cfg_sha=status()
    candidate=trusted_file(DEST)
    need(candidate==patched(original).encode('utf8'),'CANDIDATE_CONTENT_DRIFT')
    print('STAGE37B2C_CANDIDATE_VERIFIED_SOURCE_AND_CONFIG_PINNED')
    print('STAGE37B2C_UNREGISTERED_NO_LIVE_CHANGE')

if __name__=='__main__':
    try:
        need(len(sys.argv)==2 and sys.argv[1] in ('render','verify'),'USAGE')
        if sys.argv[1]=='render':render()
        else:verify()
    except Exception as exc:
        print(str(exc) if isinstance(exc,Stop) else 'STAGE37B2C_STOP_'+type(exc).__name__,file=sys.stderr)
        raise SystemExit(2)
