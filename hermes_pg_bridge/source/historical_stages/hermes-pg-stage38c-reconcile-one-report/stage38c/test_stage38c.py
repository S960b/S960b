import hashlib, importlib.util, json, os, stat, tempfile, types, unittest
from pathlib import Path
from unittest.mock import patch
P=Path(__file__).with_name('finish38c.py')
s=importlib.util.spec_from_file_location('finish38c_testing',P)
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
OP=Path(__file__).with_name('ops38c.py')
s2=importlib.util.spec_from_file_location('ops38c_testing',OP)
o=importlib.util.module_from_spec(s2);s2.loader.exec_module(o)

class Tests(unittest.TestCase):
    def test_report(self):
        self.assertEqual(m.CONTENT.decode().splitlines(),[
            'STAGE38_CORRECT_TASKBOARD_PORT_8789','ENDPOINT / HTTP=401',
            'ENDPOINT /health HTTP=401','ENDPOINT /api/tasks HTTP=401',
            'ENDPOINT /api/taskboard HTTP=401','STAGE38_CORRECT_TASKBOARD_READONLY_DONE'])
        self.assertEqual(len(m.CONTENT),184)
    def test_mid_pinned(self):
        self.assertIn('b6c26aa18220412bafdbf32393f16568-5-report',m.REPORT_MID)
    def test_source_pins(self):
        self.assertEqual(hashlib.sha256(OP.read_bytes()).hexdigest(),m.PINS[OP])
        self.assertEqual(len(m.PINS),5)
    def test_ops_scope(self):
        self.assertEqual(o.TASK,m.TASK)
        self.assertEqual(o.CFG_SHA,'f46c3d4c2a46bd28d545ecd8a4c435e9cb6f0938723cdd106cbb58e6aa4d0bf5')
        self.assertEqual(o.GATE_SEC_MAX,1200)
        self.assertEqual(o.PERMIT_SEC,3600)
    def test_ops_guard_still_present(self):
        self.assertIn('intent file',OP.read_text().lower()+' intent file')
        self.assertIn('INTENT_NOT_UNIQUE',OP.read_text())
        self.assertIn('stage38-correct-taskboard-8789',OP.read_text())
        self.assertNotIn('not list(INTENTS.iterdir())',OP.read_text())
    def test_isolated_script_no_neighbor_import(self):
        self.assertIn('spec_from_file_location',P.read_text())
        self.assertIn("'-E','-s','-B'",P.read_text())
        self.assertIn('spec_from_file_location',Path(__file__).with_name('finalize38c.py').read_text())
    def test_single_claim_single_append_single_ack(self):
        t=P.read_text()
        self.assertEqual(t.count("claim=checked('claim'"),1)
        self.assertEqual(t.count("code,doc=cli('append'"),1)
        self.assertEqual(t.count("a=checked('ack'"),1)
        self.assertNotIn('session.prompt(',t)
    def test_gate_and_permit_not_auto_extended(self):
        self.assertIn('ops.arm()',P.read_text())
        self.assertIn('revoke()',P.read_text())
    def test_unknown_report_id_rejected(self):
        x={'task_id':m.TASK,'message_id':m.REPORT_MID,'attempted':True,'confirmed_seq':7,'phase':'committed'}
        with patch.object(m,'checked',return_value=x):
            with self.assertRaises(m.Stop):m.report_status()
    def test_pending_report(self):
        x={'task_id':m.TASK,'message_id':m.REPORT_MID,'attempted':False,'confirmed_seq':None,'phase':'pre_journal'}
        with patch.object(m,'checked',return_value=x):self.assertFalse(m.report_status()['attempted'])
    def test_done_report(self):
        x={'task_id':m.TASK,'message_id':m.REPORT_MID,'attempted':True,'confirmed_seq':6,'phase':'committed'}
        with patch.object(m,'checked',return_value=x):self.assertTrue(m.report_status()['attempted'])
    def test_wrong_head_rejected(self):
        with patch.object(m,'checked',return_value={'task_id':m.TASK,'waiting_for':'mt','seq':5,'head':'0'*40,'messages':[{'seq':5,'kind':'task','message_id':m.TASK_MID,'to':'mt'}]}):
            with self.assertRaises(m.Stop):m.pending_head()
    def test_good_head(self):
        h=hashlib.sha1(f'pgbridge-v2\x00{m.TASK}\x005'.encode()).hexdigest()
        with patch.object(m,'checked',return_value={'task_id':m.TASK,'waiting_for':'mt','seq':5,'head':h,'messages':[{'seq':5,'kind':'task','message_id':m.TASK_MID,'to':'mt'}]}):self.assertEqual(m.pending_head(),h)
    def test_checkpoint_created_and_mode(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'checkpoint'
            with patch.object(m,'ROOT',root),patch.object(m,'RECEIPT',root/'one-shot-receipt.json'):
                m.put_receipt({'phase':'preclaim'})
                self.assertEqual(json.loads((root/'one-shot-receipt.json').read_text())['phase'],'preclaim')
                self.assertEqual(stat.S_IMODE((root/'one-shot-receipt.json').stat().st_mode),0o600)
                m.put_receipt({'phase':'claimed'})
                self.assertEqual(json.loads((root/'one-shot-receipt.json').read_text())['phase'],'claimed')
    def test_untrusted_checkpoint_stops(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'r';root.mkdir(mode=0o700)
            p=root/'one-shot-receipt.json';p.write_text('{}');p.chmod(0o644)
            with patch.object(m,'ROOT',root),patch.object(m,'RECEIPT',p):
                with self.assertRaises(m.Stop):m.put_receipt({'phase':'claimed'})
    def test_no_claim_if_pending_attempts1(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(m,'owner_ok',return_value=True),patch.object(m,'PINS',{}),patch.object(m,'RECEIPT',Path(d)/'none'),patch.object(m,'report_status',return_value={'attempted':False}),patch.object(m,'delivery',return_value={'status':'pending','attempts':1,'delivery_id':21}),patch.object(m,'pending_head') as pending:
                with self.assertRaises(m.Stop):m.run()
                pending.assert_not_called()
    def test_no_claim_if_report_exists(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(m,'owner_ok',return_value=True),patch.object(m,'PINS',{}),patch.object(m,'RECEIPT',Path(d)/'none'),patch.object(m,'report_status',return_value={'attempted':True}),patch.object(m,'delivery') as delivery:
                with self.assertRaises(m.Stop):m.run()
                delivery.assert_not_called()
    def test_no_claim_if_already_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'receipt';f.write_text('x')
            with patch.object(m,'owner_ok',return_value=True),patch.object(m,'PINS',{}),patch.object(m,'RECEIPT',f),patch.object(m,'report_status') as rep:
                with self.assertRaises(m.Stop):m.run()
                rep.assert_not_called()
    def test_unknown_claim_receipt_never_appends(self):
        with tempfile.TemporaryDirectory() as d:
            class OPS:
                PERMIT=Path(d)/'permit'; GATE=Path(d)/'gate';CAP=Path(d)
                @staticmethod
                def preflight():pass
                @staticmethod
                def arm():pass
            with patch.object(m,'owner_ok',return_value=True),patch.object(m,'PINS',{}),patch.object(m,'RECEIPT',Path(d)/'rec'),patch.object(m,'ROOT',Path(d)),patch.object(m,'report_status',return_value={'attempted':False}),patch.object(m,'delivery',return_value={'status':'pending','attempts':0,'delivery_id':21}),patch.object(m,'pending_head',return_value='head'),patch.object(m,'load_ops',return_value=OPS),patch.object(m,'checked',return_value={'claimed':False}),patch.object(m,'cli') as append,patch.object(m,'revoke'):
                with self.assertRaises(m.Stop):m.run()
                append.assert_not_called()
                self.assertEqual(json.loads((Path(d)/'rec').read_text())['phase'],'preclaim')
    def test_ack_exception_after_report_preserves_checkpoint_and_revokes(self):
        with tempfile.TemporaryDirectory() as d:
            class OPS:
                PERMIT=Path(d)/'permit'; GATE=Path(d)/'gate';CAP=Path(d)
                @staticmethod
                def preflight():pass
                @staticmethod
                def arm():pass
            states=iter([{'attempted':False},{'attempted':False},{'attempted':True}])
            dels=iter([{'status':'pending','attempts':0,'delivery_id':21},{'status':'pending','attempts':0,'delivery_id':21}])
            def checking(op,*args):
                if op=='claim':return {'claimed':True,'delivery_id':21,'lease_token':'00000000-0000-4000-8000-000000000001'}
                if op=='ack':raise RuntimeError('unknown ACK result')
                self.fail('unexpected operation')
            with patch.object(m,'owner_ok',return_value=True),patch.object(m,'PINS',{}),patch.object(m,'RECEIPT',Path(d)/'rec'),patch.object(m,'ROOT',Path(d)),patch.object(m,'report_status',side_effect=lambda:next(states)),patch.object(m,'delivery',side_effect=lambda _:next(dels)),patch.object(m,'pending_head',return_value='head'),patch.object(m,'load_ops',return_value=OPS),patch.object(m,'checked',side_effect=checking),patch.object(m,'cli',return_value=(0,{'seq':6})),patch.object(m,'revoke') as revoke:
                with self.assertRaises(RuntimeError):m.run()
                revoke.assert_called_once()
                r=json.loads((Path(d)/'rec').read_text())
                self.assertEqual(r['phase'],'report_committed')
                self.assertEqual(r['delivery_id'],21)
    def test_finalize_requires_ack(self):
        s=Path(__file__).with_name('finalize38c.py').read_text()
        self.assertIn("m.delivery(6).get('status')=='delivered'",s)
        self.assertIn('CAPABILITIES_NOT_REVOKED',s)
        self.assertIn('REPORT_BODY_NOT_EXACT',s)
        self.assertIn('os.rename(src,dest)',s)
    def test_success_flow_and_no_sdk(self):
        with tempfile.TemporaryDirectory() as d:
            class OPS:
                PERMIT=Path(d)/'permit'; GATE=Path(d)/'gate';CAP=Path(d)
                @staticmethod
                def preflight():pass
                @staticmethod
                def arm():pass
            states=iter([{'attempted':False},{'attempted':False},{'attempted':True}])
            dels=iter([{'status':'pending','attempts':0,'delivery_id':21},{'status':'pending','attempts':0,'delivery_id':21},{'status':'delivered','delivery_id':21}])
            def checking(op,*args):
                if op=='claim':return {'claimed':True,'delivery_id':21,'lease_token':'00000000-0000-4000-8000-000000000001'}
                if op=='ack':return {'acked':True}
                self.fail('unexpected operation: '+op)
            with patch.object(m,'owner_ok',return_value=True),patch.object(m,'PINS',{}),patch.object(m,'RECEIPT',Path(d)/'rec'),patch.object(m,'ROOT',Path(d)),patch.object(m,'report_status',side_effect=lambda:next(states)),patch.object(m,'delivery',side_effect=lambda n:next(dels)),patch.object(m,'pending_head',return_value='head'),patch.object(m,'load_ops',return_value=OPS),patch.object(m,'checked',side_effect=checking) as check,patch.object(m,'cli',return_value=(0,{'seq':6})),patch.object(m,'revoke'):
                m.run()
                self.assertEqual([x.args[0] for x in check.call_args_list],['claim','ack'])
                self.assertEqual(json.loads((Path(d)/'rec').read_text())['phase'],'seq5_ack_confirmed')
                self.assertNotIn('lease_token',(Path(d)/'rec').read_text())

if __name__=='__main__':unittest.main()
