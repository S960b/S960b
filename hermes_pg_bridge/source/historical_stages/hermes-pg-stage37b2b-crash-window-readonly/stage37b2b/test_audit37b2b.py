import importlib.util
import unittest
from pathlib import Path

P=Path(__file__).with_name('audit37b2b.py')
spec=importlib.util.spec_from_file_location('a',P)
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
BASE='''
function saveState(paths, state) { writeFileSync(paths.file, JSON.stringify(state)); }
async function dispatch(ctx, dialog, message, state) {
 const key = dialog.task_id;
 const lease = await ctx.cliCall({op:"claim",task_id:key});
 if (!lease.claimed) return false;
 state.inflight[key] = {seq:message.seq,lease_token:lease.lease_token};
 saveState(ctx.paths, state);
 const session = await ctx.sdk();
 await session.prompt('hello');
 const ack = await ctx.cliCall({op:"ack",delivery_id:lease.delivery_id});
 return ack;
}
async function reconcileAnsweredInflight(ctx, state, dialog) { return false; }
'''
class AuditTest(unittest.TestCase):
    def test_detect_claim_before_durable_save(self):
        s=a.inspect(BASE);self.assertTrue(s['risk_claim_without_durable_inflight'])
        self.assertFalse(s['save_uses_fsync'])
        self.assertTrue(s['save_direct_write'])
    def test_safe_order_detected(self):
        b=BASE.replace(' const lease = await ctx.cliCall({op:"claim",task_id:key});\n','')
        b=b.replace(' saveState(ctx.paths, state);',' saveState(ctx.paths, state);\n const lease = await ctx.cliCall({op:"claim",task_id:key});')
        # This deliberately invalid JS ordering is still enough to test lexical detection.
        self.assertFalse(a.inspect(b)['risk_claim_without_durable_inflight'])
    def test_duplicate_claim_fail_closed(self):
        b=BASE.replace(' const lease = await', ' await ctx.cliCall({op:"claim"});\n const lease = await')
        self.assertIsNone(a.inspect(b)['risk_claim_without_durable_inflight'])
    def test_missing_sdk_does_not_guess(self):
        b=BASE.replace('session.prompt(', 'session.run(')
        self.assertIsNone(a.inspect(b)['risk_claim_without_durable_inflight'])
    def test_no_captured_functions_fail_closed(self):
        with self.assertRaises(a.Stop): a.inspect('arbitrary')
    def test_dispatch_anchor_duplicate_rejected(self):
        with self.assertRaises(a.Stop): a.inspect(BASE+BASE)
    def test_rename_and_sync_detection(self):
        b=BASE.replace('writeFileSync(paths.file, JSON.stringify(state));',
            'writeFileSync(paths.tmp, JSON.stringify(state)); fsyncSync(1); renameSync(paths.tmp, paths.file);')
        r=a.inspect(b);self.assertTrue(r['save_uses_fsync']);self.assertTrue(r['save_uses_rename'])
    def test_no_source_text_in_markers(self):
        r=a.inspect(BASE);self.assertNotIn('hello',str({k:v for k,v in r.items() if k!='dispatch'}))
    def test_pin_length(self):self.assertEqual(len(a.PIN),64)
    def test_expected_ids(self):
        self.assertRegex(a.E2E,r'^task_[a-f0-9]{32}$')
        self.assertNotEqual(a.E2E,a.CANARY)
    def test_user_only_read_mode(self):self.assertTrue(hasattr(a,'read_trusted'))
    def test_report_no_lease_token(self):self.assertNotIn('lease_token',a.main.__doc__ or '')
if __name__=='__main__':unittest.main()
