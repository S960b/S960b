"""Offline policy/patch tests. Does not connect to DB or run SDK."""
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from build33i import (patch_js,patch_cli,OLD_RECONCILE_TAIL,NEW_RECONCILE_TAIL,
                      OLD_CLI_ACK,NEW_CLI_ACK,OLD_CLI_PATH,NEW_CLI_PATH,CANARY)

JS_FIXTURE='''// MOCK - NOT A REAL PLUGIN
const STATE_DIR = join(HOME, ".local/state/mt-pg-inbox-agent-stage33f");
const dryRun = false; // STAGE33F PROSPECTIVE ONLY: NOT REGISTERED
const taskIds = Object.freeze(["'''+CANARY+'''"]);
const foo = "'''+OLD_CLI_PATH+'''";
const bar = "'''+OLD_CLI_PATH+'''";
// op:"claim" op:"ack" state.inflight[key] session.prompt(
// PGV2_ROLE_BOUND_MT_SDK_ACK_20261122
async function reconcileAnsweredInflight(ctx, state, dialog) {
  const taskId = dialog.task_id, reserved = state.inflight[taskId];
  if (!reserved) return false;
  const now = Date.now();
  if (Number.isSafeInteger(reserved.reconcile_checked_ms) &&
      reserved.reconcile_checked_ms <= now &&
      reserved.reconcile_checked_ms > now - INFLIGHT_RECONCILE_MS) return false;
  reserved.reconcile_checked_ms = now;
  saveState(ctx.paths, state);
  let snapshot;
  try {
    snapshot = await ctx.cliCall({ op: "view", task_id: taskId,
                                   offset: 0, limit: 100, order: "newest" });
  } catch {return false;}
  if (!confirmedMtResponseAfter(snapshot, taskId, reserved)) return false;
'''+OLD_RECONCILE_TAIL+'''
export async function runTick(ctx) { return true; }
'''

class Tests(unittest.TestCase):
    def test_patch_js_honors_only_reconcile(self):
        r=patch_js(JS_FIXTURE)
        self.assertIn(NEW_RECONCILE_TAIL,r)
        self.assertNotIn(OLD_RECONCILE_TAIL,r)
        self.assertEqual(r.count(NEW_CLI_PATH),2)
        self.assertNotIn(OLD_CLI_PATH,r)
    def test_patch_js_reject_missing_guard(self):
        with self.assertRaises(RuntimeError):patch_js(JS_FIXTURE.replace('session.prompt(','session_PROMPT('))
    def test_patch_js_reject_duplicate_tail(self):
        with self.assertRaises(RuntimeError):patch_js(JS_FIXTURE.replace('export async function runTick',OLD_RECONCILE_TAIL+'\nexport async function runTick'))
    def test_patch_js_reject_drift(self):
        with self.assertRaises(RuntimeError):patch_js(JS_FIXTURE.replace('inflight_reconciled_confirmed_response','inflight_reconciled_something_else'))
    def test_patch_js_reject_wrong_cli_count(self):
        with self.assertRaises(RuntimeError):patch_js(JS_FIXTURE.replace('const foo = "'+OLD_CLI_PATH+'";', 'const foo = "altered";'))
    def test_patch_cli_one_bounded_ack(self):
        from pathlib import Path
        src=Path(__file__).with_name('stage33f_cli_reference.py')
        raw=src.read_text()
        r=patch_cli(raw)
        self.assertIn(NEW_CLI_ACK,r)
        self.assertNotIn(OLD_CLI_ACK,r)
        self.assertEqual(r.count("if op=='ack':"),1)
    def test_patch_cli_reject_drift(self):
        with self.assertRaises(RuntimeError):patch_cli('different')
    def test_cli_security_boundary_pinned(self):
        raw=Path(__file__).with_name('stage33f_cli_reference.py')
        r=patch_cli(raw.read_text())
        self.assertIn('delivery_status(CANARY,5)',r)
        self.assertIn("args.consumer==CONSUMER",r)
        self.assertIn("status.get('delivery_id')==args.delivery_id",r)
        self.assertNotIn('claim_message(',NEW_CLI_ACK)
    def test_no_sdk_resend_on_reconcile(self):
        self.assertNotIn('session.prompt(',NEW_RECONCILE_TAIL)
        self.assertNotIn('dispatch(',NEW_RECONCILE_TAIL)
        self.assertNotIn('op:"claim"',NEW_RECONCILE_TAIL)
    def test_clear_inflight_after_ack_only(self):
        s=NEW_RECONCILE_TAIL
        self.assertLess(s.index('acked?.acked !== true'),s.index('delete state.inflight[taskId]'))
    def test_no_secret_in_log(self):
        s=NEW_RECONCILE_TAIL
        self.assertNotIn('JSON.stringify(reserved)',s)
        self.assertNotIn('ctx.log(reserved',s)
    def test_old_cli_fingerprint(self):
        from build33i import EXPECTED_CLI_SHA
        self.assertEqual(len(EXPECTED_CLI_SHA),64)
    def test_old_plugin_fingerprint(self):
        from build33i import EXPECTED_PLUGIN_SHA
        self.assertEqual(len(EXPECTED_PLUGIN_SHA),64)

if __name__=='__main__':unittest.main()
