import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

P=Path(__file__).with_name('finish38b.py')
spec=importlib.util.spec_from_file_location('finish38b_under_test',P)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class Tests(unittest.TestCase):
    def test_report_exact(self):
        self.assertEqual(m.CONTENT.decode().splitlines(),[
            'STAGE38_CORRECT_TASKBOARD_PORT_8789',
            'ENDPOINT / HTTP=401',
            'ENDPOINT /health HTTP=401',
            'ENDPOINT /api/tasks HTTP=401',
            'ENDPOINT /api/taskboard HTTP=401',
            'STAGE38_CORRECT_TASKBOARD_READONLY_DONE'])
    def test_id_has_one_report(self):
        self.assertTrue(m.MID.endswith('-5-report'))
        self.assertEqual(m.HEAD_VERSION,5)
    def test_pinned_head_hash(self):
        h=hashlib.sha1(f'pgbridge-v2\x00{m.TASK}\x005'.encode()).hexdigest()
        with patch.object(m,'checked_cli',return_value={'task_id':m.TASK,'waiting_for':'mt','seq':5,'head':h,'messages':[{'seq':5,'message_id':m.TASK_MID,'kind':'task','from':'chatgpt','to':'mt'}]}):
            self.assertEqual(m.get_pending(),h)
    def test_head_reject_wrong_message(self):
        h=hashlib.sha1(f'pgbridge-v2\x00{m.TASK}\x005'.encode()).hexdigest()
        with patch.object(m,'checked_cli',return_value={'task_id':m.TASK,'waiting_for':'mt','seq':5,'head':h,'messages':[{'seq':5,'message_id':'other','kind':'task','from':'chatgpt','to':'mt'}]}):
            with self.assertRaises(m.Stop):m.get_pending()
    def test_status_uncommitted(self):
        with patch.object(m,'checked_cli',return_value={'task_id':m.TASK,'message_id':m.MID,'attempted':False,'confirmed_seq':None,'phase':'pre_journal'}):
            self.assertFalse(m.get_status()['attempted'])
    def test_status_committed(self):
        with patch.object(m,'checked_cli',return_value={'task_id':m.TASK,'message_id':m.MID,'attempted':True,'confirmed_seq':6,'phase':'committed'}):
            self.assertTrue(m.get_status()['attempted'])
    def test_status_wrong_seq_rejected(self):
        with patch.object(m,'checked_cli',return_value={'task_id':m.TASK,'message_id':m.MID,'attempted':True,'confirmed_seq':7,'phase':'committed'}):
            with self.assertRaises(m.Stop):m.get_status()
    def test_no_append_if_report_exists(self):
        with patch.object(m,'preflight_sources'),patch.object(m,'get_status',return_value={'attempted':True}), patch.object(m,'load_ops') as ops:
            m.finish()
            ops.assert_not_called()
    def test_stop_before_arm_if_other_task(self):
        with patch.object(m,'preflight_sources'),patch.object(m,'get_status',return_value={'attempted':False}),patch.object(m,'get_pending',side_effect=m.Stop('STAGE38B_STOP_NOT_WAITING')),patch.object(m,'load_ops') as ops:
            with self.assertRaises(m.Stop):m.finish()
            ops.assert_not_called()
    def test_pinned_original_source_sha(self):
        self.assertEqual(hashlib.sha256(m.OPS.read_bytes()).hexdigest(),m.OPS_SHA)
    def test_self_contained_isolated_import(self):
        self.assertNotIn('import archived_audit',P.read_text())
        self.assertIn('spec_from_file_location',P.read_text())
    def test_fail_closed_append_no_repeat(self):
        self.assertEqual(P.read_text().count("cli('append',"),1)
        self.assertIn('revoke_safely(ops)',P.read_text())
    def test_capability_pins(self):
        self.assertTrue(len(m.CLI_SHA)==64 and len(m.READ_SHA)==64)
        self.assertEqual(m.CLI.name,'managed_agent_cli34d.py')
    def test_gate_scope(self):
        self.assertIn('preflight()',P.read_text())
        self.assertIn('ops.arm()',P.read_text())
    def test_cli_python_uses_non_isolated_neighbor_import(self):
        self.assertIn("'-E', '-s', '-B', str(CLI)",P.read_text())
    def test_untrusted_sha_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'src.py'; p.write_text('original')
            with self.assertRaises(m.Stop):m.pin(p,'0'*64)

if __name__=='__main__':unittest.main()

class FlowTests(unittest.TestCase):
    def _ops(self,d):
        ops=types.SimpleNamespace(PERMIT=Path(d)/'permit',GATE=Path(d)/'gate',REVOKE=Path('/bin/true'))
        ops.preflight=lambda:None
        ops.arm=lambda:None
        return ops
    def test_one_append_and_then_confirm(self):
        with tempfile.TemporaryDirectory() as td:
            statuses=iter([{'attempted':False},{'attempted':False},{'attempted':True}])
            with patch.object(m,'preflight_sources'),patch.object(m,'get_status',side_effect=lambda:next(statuses)),patch.object(m,'get_pending',return_value='head'),patch.object(m,'load_ops',return_value=self._ops(td)),patch.object(m,'cli',return_value=(0,{'seq':6,'version':6})),patch.object(m,'CAP',Path(td)):
                m.finish()
                self.assertFalse(list(Path(td).glob('.stage38b-*')))
    def test_commit_unknown_is_status_checked_not_retried(self):
        with tempfile.TemporaryDirectory() as td:
            statuses=iter([{'attempted':False},{'attempted':False},{'attempted':True}])
            with patch.object(m,'preflight_sources'),patch.object(m,'get_status',side_effect=lambda:next(statuses)),patch.object(m,'get_pending',return_value='head'),patch.object(m,'load_ops',return_value=self._ops(td)),patch.object(m,'cli',return_value=(2,{'error':'report_commit_unconfirmed'})) as mock_cli,patch.object(m,'CAP',Path(td)):
                m.finish()
                self.assertEqual(mock_cli.call_count,1)
    def test_failure_revokes_after_arm(self):
        with tempfile.TemporaryDirectory() as td:
            statuses=iter([{'attempted':False},{'attempted':False}])
            with patch.object(m,'preflight_sources'),patch.object(m,'get_status',side_effect=lambda:next(statuses)),patch.object(m,'get_pending',return_value='head'),patch.object(m,'load_ops',return_value=self._ops(td)),patch.object(m,'cli',side_effect=RuntimeError('simulated error')),patch.object(m,'CAP',Path(td)),patch.object(m,'revoke_safely') as revoker:
                with self.assertRaises(RuntimeError):m.finish()
                revoker.assert_called_once()
    def test_concurrent_completed_report_skips_append(self):
        with tempfile.TemporaryDirectory() as td:
            statuses=iter([{'attempted':False},{'attempted':True}])
            with patch.object(m,'preflight_sources'),patch.object(m,'get_status',side_effect=lambda:next(statuses)),patch.object(m,'get_pending',return_value='head'),patch.object(m,'load_ops',return_value=self._ops(td)),patch.object(m,'cli') as mock_cli:
                m.finish();mock_cli.assert_not_called()
