#!/usr/bin/env python3
import unittest, sys, pathlib
sys.path.insert(0,str(pathlib.Path(__file__).parent))
import render37b2c as r
DISPATCH='''async function dispatch(ctx, dialog, message, state) {
  const key=dialog.task_id;
  const lease = await ctx.cliCall({op:"claim",task_id:key,seq:message.seq,message_id:message.message_id});
  state.inflight[key] = {seq:message.seq,message_id:message.message_id,delivery_id:lease.delivery_id};
  saveState(ctx.paths, state);
  await session.prompt({});
  if (true) throw Error('managed_report_not_confirmed');
  const acked = await ctx.cliCall({op:"ack", task_id:key, seq:message.seq, delivery_id:lease.delivery_id,lease_token:lease.lease_token});
  if (acked?.acked !== true) return false;
  state.seen[key]=message.seq;
  delete state.inflight[key];
  saveState(ctx.paths, state);
  return true;
}
'''
RECONCILE='''async function reconcileAnsweredInflight(ctx, state, dialog) {
  const taskId=dialog.task_id;const reserved=state.inflight[taskId];
  if (ack?.acked !== true) return false;
  state.seen[taskId]=reserved.seq;
  delete state.inflight[taskId];
  saveState(ctx.paths, state);
  ctx.log("managed_inflight_ack_confirmed " + taskId + " seq=" + reserved.seq);
  return true;
}
'''
TICK='''export async function runTick(ctx) {
  const holder=acquireLock(ctx.paths,ctx.log);
  if(!holder)return {status:'locked'};
  try {
    const state=loadState(ctx.paths);let cleared=0,held=0,dispatched=0;
    for (const taskId of Object.keys(state.inflight).sort()) {
      if (await reconcileAnsweredInflight(ctx,state,{task_id:taskId})) cleared++;
      else held++;
    }
    const dialogs = await listAll(ctx.cliCall);
    for (const dialog of dialogs) {
      if (dispatched >= MAX_DISPATCH_PER_TICK) break;
      if (state.inflight[dialog.task_id]) continue;
      let snapshot=await ctx.cliCall({op:'view',task_id:dialog.task_id});
      const msg=latestPending(snapshot,state.seen[dialog.task_id]||0);
      if (!msg)continue;
      const full=await ctx.cliCall({op:'read',task_id:dialog.task_id,seq:msg.seq});
      if (ctx.dryRun) continue;
      try { if (await dispatch(ctx, dialog, full, state)) dispatched++; }
      catch { ctx.log('uncertain'); }
    }
    return {status:'ok',dispatched,held,cleared};
  }finally {releaseLock(ctx.paths,holder)}
}
'''
TEXT=DISPATCH+RECONCILE+TICK+'export function diagnosticContext(o){}\n'
class TestPatch(unittest.TestCase):
 def test_patch_success(self):
  v=r.patched(TEXT);self.assertIn('stage37b2cWriteIntent(key, message.seq, message.message_id)',v)
 def test_preclaim_before_claim(self):
  v=r.patched(TEXT);self.assertLess(v.index('stage37b2cWriteIntent(key,'),v.index('op:"claim"'))
 def test_guard_uncertain(self):self.assertIn('stage37b2c_uncertain_claim_hold',r.patched(TEXT))
 def test_fsync_helper_imported(self):self.assertIn('import { stage37b2cIntentPresent,',r.patched(TEXT))
 def test_reconcile_clears_after_state_saved(self):
  v=r.patched(TEXT);x=v.split(r.RECONCILE)[1].split(r.TICK)[0];self.assertLess(x.index('saveState(ctx.paths, state);'),x.index('stage37b2cFinalizeAfterConfirmedAck'))
 def test_dispatch_success_clears(self):self.assertIn('stage37b2cFinalizeAfterConfirmedAck(state, dialog.task_id, full.seq',r.patched(TEXT))
 def test_candidate_unregistered_source_pinned(self):self.assertEqual(len(r.SOURCE_SHA),64)
 def test_reject_missing_ack(self):
  with self.assertRaises(r.Stop):r.patched(TEXT.replace('op:"ack"','op:"no-ack"'))
 def test_reject_second_claim(self):
  with self.assertRaises(r.Stop):r.patched(TEXT.replace('const lease = await','const lease2 = await ctx.cliCall({op:"claim"});\n  const lease = await'))
 def test_reject_no_report_proof(self):
  with self.assertRaises(r.Stop):r.patched(TEXT.replace('managed_report_not_confirmed','bad'))
 def test_reject_claim_after_sdk(self):
  with self.assertRaises(r.Stop):r.patched(TEXT.replace('await session.prompt({});','await session.prompt({});\n  const bad=await ctx.cliCall({op:"claim"});'))
 def test_reject_no_seen(self):
  with self.assertRaises(r.Stop):r.patched(TEXT.replace('state.seen[key]=message.seq;',''))
 def test_reject_guard_anchor(self):
  with self.assertRaises(r.Stop):r.patched(TEXT.replace('if (state.inflight[dialog.task_id]) continue;','if(state.inflight[dialog.task_id])continue;'))
 def test_reject_success_anchor(self):
  with self.assertRaises(r.Stop):r.patched(TEXT.replace('try { if (await dispatch(ctx, dialog, full, state)) dispatched++; }','try{await dispatch();}'))
 def test_reject_reconcile_ack(self):
  with self.assertRaises(r.Stop):r.patched(TEXT.replace('if (ack?.acked !== true) return false;',''))
 def test_reject_ordering(self):
  with self.assertRaises(r.Stop):r.patched(TEXT.replace('delete state.inflight[taskId];','delete state.inflight[taskId];\n  delete state.inflight[taskId];')) if False else r.patched(TEXT.replace('saveState(ctx.paths, state);\n  ctx.log("managed_inflight_ack_confirmed', 'ctx.log("managed_inflight_ack_confirmed'))
 def test_reject_missing_tick(self):
  with self.assertRaises(r.Stop):r.patched(TEXT.replace(r.TICK,'function runTick(ctx) {'))
 def test_no_live_config_edit_strings(self):
  s=pathlib.Path(__file__).with_name('render37b2c.py').read_text()
  self.assertNotIn('systemctl',s);self.assertNotIn('os.replace(CONFIG',s)
 def test_intent_has_no_sql_sdk(self):
  s=pathlib.Path(__file__).with_name('intent37b2c.mjs').read_text()
  for x in ('SELECT bridge.','session.prompt(', 'managed-writes-stage34d-armed'):
   self.assertNotIn(x,s)
 def test_intent_write_uses_exclusive_create(self):
  s=pathlib.Path(__file__).with_name('intent37b2c.mjs').read_text();self.assertIn('O_EXCL',s)
 def test_intent_fsync_file_dir(self):
  s=pathlib.Path(__file__).with_name('intent37b2c.mjs').read_text();self.assertIn('fs.fsyncSync(fd)',s);self.assertIn('syncDir(directory)',s)
if __name__=='__main__':unittest.main()
