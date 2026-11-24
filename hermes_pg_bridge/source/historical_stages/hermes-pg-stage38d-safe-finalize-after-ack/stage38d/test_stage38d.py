import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

BASE=Path(__file__).parent
spec=importlib.util.spec_from_file_location('stage38d_test_mod',BASE/'finalize38d.py')
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class Stage38dTests(unittest.TestCase):
    def test_ack_pinned(self):
        self.assertEqual(hashlib.sha256(mod.OPERATOR_ACK.read_bytes()).hexdigest(),mod.EXPECTED_ACK_SHA)
    def test_sources_pinned(self):
        for filename,pin in [('finish38c.py',mod.ORIGINAL_FINISH_SHA),('ops38c.py',mod.ORIGINAL_OPS_SHA)]:
            self.assertEqual(hashlib.sha256((BASE/filename).read_bytes()).hexdigest(),pin)
    def test_ack_content_exact(self):
        val=mod.read_operator_ack('6d9ce1ff8e701eef323d75b88d4a2667b540331a00baebcf58d4277e6146a93f')
        self.assertEqual(val['seq6']['delivery_id'],22)
        self.assertFalse(val['dialogue']['dispatch_enabled'])
    def test_ack_wrong_body_refused(self):
        with self.assertRaises(mod.Stop):mod.read_operator_ack('ab'*32)
    def test_ack_schema_bad_status_refused(self):
        with tempfile.TemporaryDirectory() as t:
            fake=Path(t)/'ack'; orig=json.loads(mod.OPERATOR_ACK.read_text());orig['seq6']['ack']=False
            fake.write_text(json.dumps(orig));fake.chmod(0o600)
            with patch.object(mod,'OPERATOR_ACK',fake),patch.object(mod,'EXPECTED_ACK_SHA',mod.sha(fake.read_bytes())):
                with self.assertRaises(mod.Stop):mod.read_operator_ack(orig['report_body_sha256'])
    def test_file_symlink_refused(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'orig';p.write_text('a');p.chmod(0o600)
            link=Path(t)/'link';link.symlink_to(p)
            with self.assertRaises(mod.Stop):mod.trusted_file(link)
    def test_world_writable_refused(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'x';p.write_text('b');p.chmod(0o666)
            with self.assertRaises(mod.Stop):mod.trusted_file(p)
    def test_private_directory_checks(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'d';p.mkdir(mode=0o700)
            mod.private_dir(p)
            p.chmod(0o755)
            with self.assertRaises(mod.Stop):mod.private_dir(p)
    def test_archive_atomic(self):
        with tempfile.TemporaryDirectory() as t:
            srcdir=Path(t)/'intent';srcdir.mkdir(mode=0o700)
            destdir=Path(t)/'recovery';destdir.mkdir(mode=0o700)
            src=srcdir/(mod.TASK+'.json')
            payload={'version':1,'phase':'claim_outcome_uncertain','task_id':mod.TASK,'seq':5,'message_id':mod.EXPECTED_MSG}
            src.write_text(json.dumps(payload));src.chmod(0o600)
            dst=destdir/'archived-intent-seq5-after-ack.json'
            mod.archive_intent(src,dst)
            self.assertFalse(src.exists());self.assertEqual(json.loads(dst.read_text()),payload)
    def test_archive_reject_extra_intent(self):
        with tempfile.TemporaryDirectory() as t:
            srcdir=Path(t)/'intent';srcdir.mkdir(mode=0o700)
            destdir=Path(t)/'recovery';destdir.mkdir(mode=0o700)
            src=srcdir/(mod.TASK+'.json')
            payload={'version':1,'phase':'claim_outcome_uncertain','task_id':mod.TASK,'seq':5,'message_id':mod.EXPECTED_MSG}
            src.write_text(json.dumps(payload));src.chmod(0o600)
            (srcdir/'other').write_text('x')
            with self.assertRaises(mod.Stop):mod.archive_intent(src,destdir/'archived')
            self.assertTrue(src.exists())
    def test_archive_reject_modified_intent(self):
        with tempfile.TemporaryDirectory() as t:
            srcdir=Path(t)/'intent';srcdir.mkdir(mode=0o700)
            destdir=Path(t)/'recovery';destdir.mkdir(mode=0o700)
            src=srcdir/(mod.TASK+'.json')
            src.write_text(json.dumps({'phase':'claimed','task_id':mod.TASK}));src.chmod(0o600)
            with self.assertRaises(mod.Stop):mod.archive_intent(src,destdir/'archived')
            self.assertTrue(src.exists())
    def test_archive_no_overwrite(self):
        with tempfile.TemporaryDirectory() as t:
            srcdir=Path(t)/'intent';srcdir.mkdir(mode=0o700)
            destdir=Path(t)/'recovery';destdir.mkdir(mode=0o700)
            src=srcdir/(mod.TASK+'.json')
            src.write_text(json.dumps({'version':1,'phase':'claim_outcome_uncertain','task_id':mod.TASK,'seq':5,'message_id':mod.EXPECTED_MSG}));src.chmod(0o600)
            dst=destdir/'archived';dst.write_text('existing');dst.chmod(0o600)
            with self.assertRaises(mod.Stop):mod.archive_intent(src,dst)
            self.assertEqual(dst.read_text(),'existing')
    def test_never_requests_agent_report_delivery(self):
        s=(BASE/'finalize38d.py').read_text()
        self.assertIn('delivery5=m.delivery(5)',s)
        self.assertNotIn('m.delivery(6)',s)
        self.assertNotIn("checked('ack'",s)
        self.assertNotIn("checked('append'",s)
    def test_never_arms(self):
        s=(BASE/'finalize38d.py').read_text()
        self.assertNotIn('ops.arm()',s)
        self.assertNotIn('set_dispatch(',s)
    def test_abs_file_import_isolated(self):
        p=subprocess.run([sys.executable,'-IB','-c',
           "import importlib.util; p='"+str(BASE/'finalize38d.py')+"'; s=importlib.util.spec_from_file_location('stage38d',p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(m.TASK)"],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertIn(mod.TASK,p.stdout)
    def test_main_refuses_root(self):
        # In tests, main() should signal STOP before any import/pin or mutations.
        with patch.object(mod,'run',side_effect=mod.Stop('STAGE38D_STOP_TEST')):
            self.assertEqual(mod.main(),2)
    def test_ack_attestation_is_historical(self):
        d=json.loads(mod.OPERATOR_ACK.read_text())
        self.assertIn('not independently queried',d['scope'])
    def test_sha_matches_exact_report(self):
        m=mod.load_module('test_pin_finish38c',BASE/'finish38c.py',mod.ORIGINAL_FINISH_SHA)
        self.assertEqual(mod.sha(m.CONTENT),'6d9ce1ff8e701eef323d75b88d4a2667b540331a00baebcf58d4277e6146a93f')
    def test_source_scope(self):
        m=mod.load_module('test_pin_finish38c2',BASE/'finish38c.py',mod.ORIGINAL_FINISH_SHA)
        self.assertEqual(m.TASK,mod.TASK)
        self.assertEqual(m.REPORT_MID,mod.REPORT_MID)
    def test_ops_scope(self):
        o=mod.load_module('test_pin_ops38c',BASE/'ops38c.py',mod.ORIGINAL_OPS_SHA)
        self.assertEqual(o.TASK,mod.TASK)
        self.assertTrue(o.CFG_SHA.startswith('f46c3d4c2a46'))
    def test_script_doesnt_assume_script_sys_path(self):
        s=(BASE/'finalize38d.py').read_text()
        self.assertIn('importlib.util.spec_from_file_location',s)
        self.assertNotIn('from finish38c import ',s)
    def test_only_atomic_rename_local_mutation(self):
        s=(BASE/'finalize38d.py').read_text()
        self.assertIn('os.rename(src,dst)',s)
        self.assertNotIn('os.remove(src)',s)
    def test_end_to_end_mocked_readonly_then_archive(self):
        import types
        with tempfile.TemporaryDirectory() as t:
            base=Path(t)
            srcdir=base/'intents';srcdir.mkdir(mode=0o700)
            root=base/'root';root.mkdir(mode=0o700)
            receipt=root/'one-shot-receipt.json'
            receipt.write_text(json.dumps(mod.EXPECTED_RECEIPT));receipt.chmod(0o600)
            src=srcdir/(mod.TASK+'.json')
            src.write_text(json.dumps({'version':1,'phase':'claim_outcome_uncertain','task_id':mod.TASK,'seq':5,'message_id':mod.EXPECTED_MSG}));src.chmod(0o600)
            content=('STAGE38_CORRECT_TASKBOARD_PORT_8789\nENDPOINT / HTTP=401\nENDPOINT /health HTTP=401\nENDPOINT /api/tasks HTTP=401\nENDPOINT /api/taskboard HTTP=401\nSTAGE38_CORRECT_TASKBOARD_READONLY_DONE\n').encode()
            calls=[]
            def cli(method,*args):
                calls.append(method)
                if method=='peek':
                    return {'task_id':mod.TASK,'seq':6,'waiting_for':'chatgpt','messages':[{'seq':6,'message_id':mod.REPORT_MID,'body':content.decode(),'kind':'report','from':'mt','to':'chatgpt'}]}
                raise AssertionError('unexpected call')
            m=types.SimpleNamespace(TASK=mod.TASK,REPORT_MID=mod.REPORT_MID,PINS={},RECEIPT=receipt,
                ROOT=root,CONTENT=content,pin=lambda *args:None,
                report_status=lambda:{'attempted':True,'phase':'committed','confirmed_seq':6},
                delivery=lambda n: {'status':'delivered','attempts':1,'delivery_id':21} if n==5 else (_ for _ in ()).throw(AssertionError('delivery6 is forbidden')),
                checked=cli)
            ops=types.SimpleNamespace(PERMIT=base/'permit',GATE=base/'gate',INTENTS=srcdir,preflight=lambda:None)
            original_require=mod.require
            with patch.object(mod,'load_module',side_effect=[m,ops]),patch.object(mod,'require',side_effect=lambda val,name:None if name=='WRONG_USER' else original_require(val,name)),patch.object(sys,'argv',['finalize38d.py']):
                mod.run()
            self.assertEqual(calls,['peek'])
            self.assertFalse(src.exists())
            self.assertTrue((root/'archived-intent-seq5-after-ack.json').exists())
    def test_end_to_end_wrong_report_no_archive(self):
        import types
        with tempfile.TemporaryDirectory() as t:
            base=Path(t);srcdir=base/'intents';srcdir.mkdir(mode=0o700)
            root=base/'root';root.mkdir(mode=0o700)
            rec=root/'one-shot-receipt.json';rec.write_text(json.dumps(mod.EXPECTED_RECEIPT));rec.chmod(0o600)
            src=srcdir/(mod.TASK+'.json');src.write_text(json.dumps({'version':1,'phase':'claim_outcome_uncertain','task_id':mod.TASK,'seq':5,'message_id':mod.EXPECTED_MSG}));src.chmod(0o600)
            m=types.SimpleNamespace(TASK=mod.TASK,REPORT_MID=mod.REPORT_MID,PINS={},RECEIPT=rec,ROOT=root,
                CONTENT=b'good',pin=lambda *args:None,report_status=lambda:{'attempted':True,'phase':'committed','confirmed_seq':6},
                delivery=lambda n:{'status':'delivered','attempts':1,'delivery_id':21},
                checked=lambda *args:{'task_id':mod.TASK,'seq':6,'waiting_for':'chatgpt','messages':[{'seq':6,'message_id':mod.REPORT_MID,'body':'wrong','kind':'report','from':'mt','to':'chatgpt'}]})
            ops=types.SimpleNamespace(PERMIT=base/'permit',GATE=base/'gate',INTENTS=srcdir,preflight=lambda:None)
            original_require=mod.require
            with patch.object(mod,'load_module',side_effect=[m,ops]),patch.object(mod,'require',side_effect=lambda val,name:None if name=='WRONG_USER' else original_require(val,name)),patch.object(sys,'argv',['finalize38d.py']):
                with self.assertRaises(mod.Stop):mod.run()
            self.assertTrue(src.exists())
    def test_checkpoint_exact(self):
        self.assertEqual(mod.EXPECTED_RECEIPT['seq'],5)
        self.assertEqual(mod.EXPECTED_RECEIPT['phase'],'seq5_ack_confirmed')

if __name__=='__main__':unittest.main()
