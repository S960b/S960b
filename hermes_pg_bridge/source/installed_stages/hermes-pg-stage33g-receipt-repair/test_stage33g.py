import json
import re
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from verify33g import REQUIRED, VerifyError, verify

TASK='task_f2c64cfdf7894537b35a19a6def977c6'
TASK_MID='pgv2-stage33f-sdk-task-v1'
REPORT_MID='pgv2-stage33f-sdk-report-v1'
ACK='PGV2_ROLE_BOUND_MT_SDK_ACK_20261122'


def sample(status='leased',**kw):
    d={'stage':'33G','seq5_status':status}
    d.update({k:True for k in REQUIRED})
    d.update(kw)
    return json.dumps(d)

class TestVerifier(unittest.TestCase):
    def test_pre_lease(self): self.assertEqual(verify(sample()),'leased')
    def test_pre_done(self): self.assertEqual(verify(sample('delivered')),'delivered')
    def test_post_requires_done(self):
        with self.assertRaises(VerifyError):verify(sample(),after=True)
        self.assertEqual(verify(sample('delivered'),after=True),'delivered')
    def test_reject_missing_receipt(self):
        for k in REQUIRED:
            with self.subTest(k=k),self.assertRaises(VerifyError):
                verify(sample(**{k:False}))
    def test_reject_missing_stage(self):
        with self.assertRaises(VerifyError):verify(sample().replace('33G','33H'))
    def test_reject_noise(self):
        with self.assertRaises(VerifyError):verify(sample()+'\nCAUTION\n')
    def test_reject_wrong_status(self):
        with self.assertRaises(VerifyError):verify(sample('pending'))

class TestSQLGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pre=(ROOT/'sql/01_preflight_readonly.sql').read_text()
        cls.sql=(ROOT/'sql/02_ack_verified_seq5_once.sql').read_text()
    def test_only_one_task_id(self):
        for text in (self.pre,self.sql):
            self.assertIn(TASK,text)
            self.assertEqual(set(re.findall(r'task_[0-9a-f]{32}',text)),{TASK})
    def test_fixed_ids_and_ack(self):
        for text in (self.pre,self.sql):
            for mid in (TASK_MID,REPORT_MID,ACK):self.assertIn(mid,text)
    def test_body_matches_stage33fb(self):
        import importlib.util
        src=Path('/mnt/data/_pginspect/hermes-pg-stage33fb-mcp-ready/logic33fb.py')
        if src.exists():
            spec=importlib.util.spec_from_file_location('stage33fb_logic',src)
            m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
            expected=m.BODY
        else:
            expected='Безопасная проверка доставки через роль PostgreSQL bridge_agent и MultiTool SDK. Это не просьба выполнять команды ОС, обращаться в сеть или изменять файлы, кроме временного UTF-8 файла с ответом. Используй только указанный в MT Inbox локальный CLI Stage 33F для публикации отчёта в текущем PostgreSQL-диалоге. Тело отчёта строго одна строка: PGV2_ROLE_BOUND_MT_SDK_ACK_20261122. Никакие другие задания не выполняй.'
        for text in (self.pre,self.sql):
            found=re.findall(r'\$taskbody\$(.*?)\$taskbody\$',text,re.S)
            self.assertEqual(found,[expected])
    def test_preflight_read_only(self):
        self.assertIn('BEGIN TRANSACTION READ ONLY',self.pre)
        for bad in ('UPDATE ', 'INSERT ', 'DELETE ', 'GRANT ', 'ALTER TABLE', 'DROP TABLE'):
            self.assertNotIn(bad,self.pre.upper())
    def test_one_and_only_one_ack_call(self):
        self.assertEqual(self.sql.count('SELECT bridge.ack_outbox('),1)
        self.assertNotIn('UPDATE bridge.outbox',self.sql)
        self.assertNotIn('DELETE FROM bridge.',self.sql)
        self.assertNotIn('CREATE FUNCTION',self.sql)
    def test_explicit_lease_and_report_guards(self):
        for phrase in ('v_outbox.status <> \'leased\'',
                       'v_outbox.lease_owner <> \'mt-pg-inbox\'',
                       'v_report.sender <> \'agent\'',
                       "v_task_msg.sender <> 'chatgpt'",
                       "v_seq6.status <> 'pending'",
                       'v_dialog.last_seq <> 6',
                       "v_dialog.next_actor <> 'chatgpt'",
                       "v_outbox.lease_token IS NULL"):
            self.assertIn(phrase,self.sql)
    def test_single_transaction_fail_closed(self):
        self.assertIn('BEGIN;',self.sql)
        self.assertTrue(self.sql.strip().endswith('COMMIT;'))
        self.assertIn('ON_ERROR_STOP on',self.sql)
        self.assertIn('RAISE EXCEPTION',self.sql)
        self.assertIn('STAGE33G_ALREADY_DELIVERED_NO_CHANGE',self.sql)
    def test_no_insecure_ssh(self):
        for name in ('45_readonly_preflight.sh','46_ack_exact_once.sh','47_verify_only.sh'):
            s=(ROOT/name).read_text()
            self.assertIn('StrictHostKeyChecking=yes',s)
            self.assertIn('PG_V2_SSH_TARGET',s)
            self.assertNotIn('sshpass',s)

if __name__=='__main__':unittest.main(verbosity=2)
