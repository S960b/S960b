from __future__ import annotations
import ast
import hashlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

spec=importlib.util.spec_from_file_location('j',Path(__file__).with_name('stage33j.py'))
j=importlib.util.module_from_spec(spec);spec.loader.exec_module(j)

class TestStage33J(unittest.TestCase):
    def test_fixed_actor_scope(self):
        self.assertEqual(j.CANARY,'task_f2c64cfdf7894537b35a19a6def977c6')
        self.assertEqual(j.CONF_SHA,'e5320f6e971073d5d2c43a64ce85d165cb49dc9a94e6cf7806009ebc2b59ded1')
    def test_expected_live_code_hash_pinned(self):
        self.assertEqual(j.LIVE_SHA,'220da977562f9c0763d6b5d5640f686f54f041d357ed397c8482c0d4363f525c')
    def test_ack_only_not_generic(self):
        src=Path(j.__file__).read_text()
        self.assertIn("arg['delivery_id']==payload['delivery_id']",src)
        self.assertIn("arg['lease_token']==payload['lease_token']",src)
        self.assertIn('fixed_record(cg,ag,tid)',src)
        self.assertNotIn('bridge_app',src)
    def test_one_shot_and_private_file(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'proof';j.atom_once(p,b'{"ok":true}')
            self.assertEqual(p.read_bytes(),b'{"ok":true}')
            self.assertEqual(p.stat().st_mode & 0o077,0)
            with self.assertRaises(j.Stop):j.atom_once(p,b'again')
    def test_version_token(self):
        self.assertRegex(j.version_token(j.CANARY,6),r'^[0-9a-f]{40}$')
        self.assertNotEqual(j.version_token(j.CANARY,5),j.version_token(j.CANARY,6))
    def test_expected_message_contents(self):
        self.assertEqual(j.TASK_BODY,'STAGE33J_ISOLATED_SQL_RECEIPT_TEST_DO_NOT_EXECUTE')
        self.assertEqual(j.REPORT_BODY,'STAGE33J_AGENT_REPORT_ISOLATED_NO_SDK')
    def test_sha(self):
        self.assertEqual(j.sha(b'abc'),hashlib.sha256(b'abc').hexdigest())
    def test_no_production_state_write(self):
        src=Path(j.__file__).read_text()
        self.assertIn('sha(PROD_STATE.read_bytes())==before',src)
        self.assertNotIn('PROD_STATE.write_',src)
        self.assertNotIn('PROD_STATE.unlink',src)
    def test_no_sdk(self):
        src=Path(j.__file__).read_text()
        self.assertNotIn('session.prompt(',src)
        self.assertNotIn('session.create(',src)
        self.assertNotIn('opencode.sdk',src)
    def test_node_uses_real_plugin_function(self):
        source=Path(__file__).with_name('reconcile33j.cjs').read_text()
        self.assertIn('source=src.slice(a,c)',source)
        self.assertIn('new Function(',source)
        self.assertIn("const LIVE_SHA='220da977",source)
    def test_lost_ack_fault_injected_only_after_commit(self):
        src=Path(__file__).with_name('reconcile33j.cjs').read_text()
        self.assertLess(src.index('const receipt=ackViaRealAgent('),
                        src.index("throw new Error('SIMULATED_LOST_ACK_REPLY_AFTER_PG_COMMIT')"))
    def test_inflight_survival_is_asserted(self):
        src=Path(__file__).with_name('reconcile33j.cjs').read_text()
        self.assertIn("assert.ok(state.inflight[data.task_id],'INFLIGHT_MISSING_AFTER_LOSS')",src)
        self.assertIn('assert.equal(state.seen[data.task_id],1)',src)
    def test_repo_no_generic_dialog_filter_mutation(self):
        src=Path(j.__file__).read_text()
        self.assertNotIn("CONF.write_",src)
        self.assertNotIn('systemctl stop',src)
    def test_isolated_fixed_record_valid(self):
        class Cg:
            def creation_status(self,k):return {'task_id':'task_'+'b'*32}
            def dialog_metadata(self,tid):return {'state':'active','last_seq':2,'version':2}
            def send_status(self,tid,mid):return {'found':True,'seq':1,'sender':'chatgpt'}
            def view_dialog(self,tid,off,lim):return {'messages':[
                {'seq':2,'message_id':j.REPORT_MID,'sender':'agent','recipient':'chatgpt','body':j.REPORT_BODY},
                {'seq':1,'message_id':j.TASK_MID,'sender':'chatgpt','recipient':'agent','body':j.TASK_BODY}]}
        class Ag:
            def send_status(self,tid,mid):return {'found':True,'seq':2,'sender':'agent'}
            def delivery_status(self,tid,seq):return {'delivery_id':42,'status':'leased'}
        m,s=j.fixed_record(Cg(),Ag(),'task_'+'b'*32)
        self.assertEqual(m['version'],2)
        self.assertEqual(s['delivery_id'],42)
    def test_isolated_fixed_record_rejects_wrong_report(self):
        class Cg:
            def creation_status(self,k):return {'task_id':'task_'+'b'*32}
            def dialog_metadata(self,tid):return {'state':'active','last_seq':2,'version':2}
            def send_status(self,tid,mid):return {'found':True,'seq':1,'sender':'chatgpt'}
            def view_dialog(self,tid,off,lim):return {'messages':[
                {'seq':2,'message_id':j.REPORT_MID,'sender':'agent','recipient':'chatgpt','body':'WRONG'},
                {'seq':1,'message_id':j.TASK_MID,'sender':'chatgpt','recipient':'agent','body':j.TASK_BODY}]}
        class Ag:
            def send_status(self,tid,mid):return {'found':True,'seq':2,'sender':'agent'}
        with self.assertRaisesRegex(j.Stop,'test_message_content_mismatch'):
            j.fixed_record(Cg(),Ag(),'task_'+'b'*32)
    def test_isolated_fixed_record_rejects_foreign_task(self):
        with self.assertRaisesRegex(j.Stop,'isolated_task_id_invalid'):
            j.fixed_record(None,None,'task_../other')
    def test_lease_fixture_pins_identity(self):
        self.assertEqual(j.REQUEST_ID,'pgv2-stage33j-reconcile-fault-20261122-v1')
        self.assertNotEqual(j.TASK_MID,j.REPORT_MID)
        self.assertTrue(j.CONSUMER.startswith('stage33j-'))
    def test_no_live_config_or_service_mutation(self):
        s=Path(j.__file__).read_text()
        self.assertNotIn('systemctl --user stop',s)
        self.assertNotIn('CONF.write_text(',s)
        self.assertNotIn('CONF.unlink(',s)
    def test_commands_nonretryable(self):
        sh=Path(__file__).with_name('62_run_once.sh').read_text()
        self.assertIn('stage33j.py run-once',sh)
        self.assertNotIn('while true',sh)

if __name__=='__main__':unittest.main()
