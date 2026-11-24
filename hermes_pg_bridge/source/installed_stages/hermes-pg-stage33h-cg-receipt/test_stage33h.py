import hashlib
import json
import pathlib
import tempfile
import unittest
from unittest import mock
import verify33h as v
import source_inventory33h as src

HERE=pathlib.Path(__file__).resolve().parent

def sample(phase):
    x=dict(database_ok=True,canary_meta_ok=True,six_messages=True,seq5_exact=True,seq6_exact=True,
           seq5_status='delivered',seq5_attempts=1,seq6_status='pending',seq6_attempts=0,
           seq6_recipient='chatgpt',seq6_delivered_at_set=False,seq6_lease_null=True,outbox_count=6)
    if phase=='post':x.update(seq6_status='delivered',seq6_attempts=1,seq6_delivered_at_set=True)
    return x

class TestReceipt(unittest.TestCase):
    def test_pre(self):self.assertIn('PENDING',v.validate(sample('pre'),'pre'))
    def test_post(self):self.assertIn('DELIVERED',v.validate(sample('post'),'post'))
    def test_wrong_database(self):
        x=sample('pre');x['database_ok']=False
        self.assertRaises(v.Stop,v.validate,x,'pre')
    def test_wrong_report(self):
        x=sample('pre');x['seq6_exact']=False
        self.assertRaises(v.Stop,v.validate,x,'pre')
    def test_wrong_seq5(self):
        x=sample('pre');x['seq5_status']='leased'
        self.assertRaises(v.Stop,v.validate,x,'pre')
    def test_wrong_attempts(self):
        x=sample('post');x['seq6_attempts']=2
        self.assertRaises(v.Stop,v.validate,x,'post')
    def test_bad_lease(self):
        x=sample('pre');x['seq6_lease_null']=False
        self.assertRaises(v.Stop,v.validate,x,'pre')
    def test_bad_count(self):
        x=sample('pre');x['outbox_count']=7
        self.assertRaises(v.Stop,v.validate,x,'pre')
    def test_pre_does_not_accept_post(self):self.assertRaises(v.Stop,v.validate,sample('post'),'pre')
    def test_post_does_not_accept_pre(self):self.assertRaises(v.Stop,v.validate,sample('pre'),'post')
    def test_unknown_phase(self):self.assertRaises(v.Stop,v.validate,sample('pre'),'other')
    def test_static_sql_guards(self):
        s=(HERE/'sql/02_accept_exact_seq6_once.sql').read_text()
        for x in ('FOR UPDATE','session_user',"v_outbox5.status<>'delivered'", "v_outbox6.status<>'pending'",'bridge.ack_outbox(', 'seq=6', 'seq=5', 'COMMIT;', 'STAGE33H_EXACT_SEQ6_RECEIPT_COMMITTED'):
            self.assertIn(x,s)
        self.assertNotIn('CREATE ROLE',s)
        self.assertNotIn('GRANT ',s)
        self.assertNotIn('tombstone_dialog(',s)
    def test_preflight_is_readonly(self):
        s=(HERE/'sql/01_readonly_receipt.sql').read_text()
        self.assertIn('BEGIN READ ONLY;',s)
        for vbad in ('UPDATE bridge.', 'DELETE FROM bridge.', 'INSERT INTO bridge.'):
            self.assertNotIn(vbad,s)
    def test_runner_is_guarded(self):
        s=(HERE/'51_accept_seq6_once.sh').read_text()
        self.assertIn('50_preflight_readonly.sh',s)
        self.assertIn('52_verify_only.sh',s)
        self.assertNotIn('opencode.json',s)
        self.assertNotIn('restart',s)
    def test_source_fails_bad_hash(self):
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d)
            a=p/'plugin.mjs';b=p/'opencode.json'
            a.write_text('hello');b.write_text('{"plugin":[]}')
            with mock.patch.object(src,'PATH',a),mock.patch.object(src,'CONFIG',b):
                self.assertRaises(RuntimeError,src.inspect)
    def test_source_static_selection(self):
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d);a=p/'plugin.mjs';b=p/'opencode.json'
            a.write_text('\n'.join([
                'async function dispatch(ctx,dialog,message,state) {',
                '  state.inflight[key] = {};',
                '  const x = {op:"claim"};',
                '  const y = {op:"ack"};',
                '}',
                'function confirmedMtResponseAfter() {}',
                'async function reconcileAnsweredInflight(ctx,state,dialog) {}',
                'export async function runTick(ctx) {}',
            ])); b.write_text(json.dumps({'plugin':[str(a),'a','b','c','d','e','f']}))
            with mock.patch.object(src,'PATH',a),mock.patch.object(src,'CONFIG',b),\
                 mock.patch.object(src,'EXPECTED_PLUGIN',hashlib.sha256(a.read_bytes()).hexdigest()),\
                 mock.patch.object(src,'EXPECTED_CFG',hashlib.sha256(b.read_bytes()).hexdigest()),\
                 mock.patch.object(src.os,'geteuid',return_value=1000),\
                 mock.patch.object(src,'safe_regular',return_value=True):
                x=src.inspect()
                self.assertEqual(x['critical_marker_lines']['ack'],[4])
                self.assertFalse(x['sdk_or_pg_write_attempted'])
                self.assertEqual(len(x['source_excerpts']),6)

if __name__=='__main__':unittest.main()
