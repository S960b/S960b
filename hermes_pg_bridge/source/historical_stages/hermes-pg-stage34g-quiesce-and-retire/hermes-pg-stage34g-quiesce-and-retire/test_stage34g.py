import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

spec=importlib.util.spec_from_file_location('stage34g',Path(__file__).with_name('stage34g.py'))
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)

class PureValidationTests(unittest.TestCase):
    def setUp(self):
        self.base={'model':'something','plugin':[
            ['/opt/other.mjs',{'a':1}],
            [str(g.CANARY_JS),{'dry_run':False,'task_ids':[g.CANARY]}],
            ['/opt/more.mjs',{}],'/opt/third.mjs','/opt/fourth.mjs',
            ['/opt/fifth.mjs',{'z':2}],'/opt/sixth.mjs']}
        self.pilot=[str(g.PILOT_JS),{'dry_run':False,'tick_ms':30000,'task_ids':[g.PILOT]}]
    def blob(self,obj):return json.dumps(obj).encode()
    def test_01_accepts_seven(self):self.assertEqual(g.validate_config(self.blob(self.base),7)['model'],'something')
    def test_02_accepts_eight(self):
        self.base['plugin'].append(self.pilot)
        self.assertEqual(len(g.validate_config(self.blob(self.base),8)['plugin']),8)
    def test_03_rejects_seven_with_pilot(self):
        self.base['plugin'][-1]=self.pilot
        with self.assertRaises(g.Stop):g.validate_config(self.blob(self.base),7)
    def test_04_rejects_canary_changed(self):
        self.base['plugin'][1][1]['task_ids']=[g.PILOT]
        with self.assertRaises(g.Stop):g.validate_config(self.blob(self.base),7)
    def test_05_rejects_canary_dry_run(self):
        self.base['plugin'][1][1]['dry_run']=True
        with self.assertRaises(g.Stop):g.validate_config(self.blob(self.base),7)
    def test_06_rejects_canary_duplicate(self):
        self.base['plugin'][-1]=self.base['plugin'][1]
        with self.assertRaises(g.Stop):g.validate_config(self.blob(self.base),7)
    def test_07_rejects_legacy_git_plugin(self):
        self.base['plugin'][-1]='mt-git-inbox.mjs'
        with self.assertRaises(g.Stop):g.validate_config(self.blob(self.base),7)
    def test_08_rejects_pilot_extra_task(self):
        self.base['plugin'].append(self.pilot)
        self.pilot[1]['task_ids']=[g.PILOT,'task_'+'f'*32]
        with self.assertRaises(g.Stop):g.validate_config(self.blob(self.base),8)
    def test_09_rejects_pilot_dry_run(self):
        self.base['plugin'].append(self.pilot)
        self.pilot[1]['dry_run']=True
        with self.assertRaises(g.Stop):g.validate_config(self.blob(self.base),8)
    def test_10_rejects_wrong_count(self):
        with self.assertRaises(g.Stop):g.validate_config(self.blob(self.base),8)
    def test_11_rejects_weird_entry(self):
        self.base['plugin'][-1]=['path',4]
        with self.assertRaises(g.Stop):g.validate_config(self.blob(self.base),7)
    def test_12_rejects_old_canary_plugin(self):
        self.base['plugin'][-1]='mt-pg-inbox-canary.mjs'
        with self.assertRaises(g.Stop):g.validate_config(self.blob(self.base),7)
    def test_13_immutable_ids(self):self.assertNotEqual(g.PILOT,g.CANARY)
    def test_14_sha_pins_are_full(self):
        for x in (g.SHA7,g.SHA8,g.PILOT_JS_SHA,g.CANARY_JS_SHA):
            self.assertRegex(x,r'^[0-9a-f]{64}$')
    def test_15_exact_gate_bytes(self):self.assertEqual(g.GATE_BYTES,b'ALLOW_MANAGED_WRITES_AFTER_EXCLUSIVE_SDK_REVIEW\n')

class FileAndGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
    def path(self,n):return self.root/n
    def test_16_journal_write_once(self):
        p=self.path('audit.json')
        g.journal(p,{'test':True})
        self.assertTrue(json.loads(p.read_text())['test'])
        self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o600)
        with self.assertRaises(g.Stop):g.journal(p,{'test':False})
    def test_17_atomic_nonreplace(self):
        p=self.path('file');g.atomic_write(p,b'first')
        self.assertEqual(p.read_bytes(),b'first')
        with self.assertRaises(g.Stop):g.atomic_write(p,b'other')
        self.assertEqual(p.read_bytes(),b'first')
    def test_18_atomic_replace(self):
        p=self.path('file');p.write_bytes(b'before');p.chmod(0o600)
        g.atomic_write(p,b'after',replace=True)
        self.assertEqual(p.read_bytes(),b'after')
    def test_19_gate_rejects_wrong_content(self):
        p=self.path('gate');p.write_bytes(b'evil');p.chmod(0o600)
        with mock.patch.object(g,'GATE',p):
            with self.assertRaises(g.Stop):g.gate_valid()
    def test_20_gate_rejects_world_readable(self):
        p=self.path('gate');p.write_bytes(g.GATE_BYTES);p.chmod(0o644)
        with mock.patch.object(g,'GATE',p):
            with self.assertRaises(g.Stop):g.gate_valid()
    def test_21_gate_rejects_symlink(self):
        target=self.path('target');target.write_bytes(g.GATE_BYTES);target.chmod(0o600)
        p=self.path('gate');p.symlink_to(target)
        with mock.patch.object(g,'GATE',p):
            with self.assertRaises(g.Stop):g.gate_valid()
    def test_22_gate_absence_rejects_symlink(self):
        p=self.path('gate');p.symlink_to(self.path('missing'))
        with mock.patch.object(g,'GATE',p):
            with self.assertRaises(g.Stop):g.gate_absent()
    def test_23_gate_revoke_after_read_only_db_confirm(self):
        gate=self.path('gate');gate.write_bytes(g.GATE_BYTES);gate.chmod(0o600)
        attempt=self.path('attempt');attempt.write_text('{}');attempt.chmod(0o600)
        receipt=self.path('receipt')
        with mock.patch.multiple(g,GATE=gate,DISABLE_ATTEMPT=attempt,GATE_REVOKED=receipt), \
             mock.patch.object(g,'confirmed_disabled',return_value={'dispatch_enabled':False}):
            g.revoke_gate_after_verified_db()
            self.assertFalse(gate.exists());self.assertTrue(receipt.exists())
            g.revoke_gate_after_verified_db()
    def test_24_gate_revoke_denied_if_db_not_disabled(self):
        gate=self.path('gate');gate.write_bytes(g.GATE_BYTES);gate.chmod(0o600)
        attempt=self.path('attempt');attempt.write_text('{}');attempt.chmod(0o600)
        with mock.patch.multiple(g,GATE=gate,DISABLE_ATTEMPT=attempt,GATE_REVOKED=self.path('receipt')), \
             mock.patch.object(g,'confirmed_disabled',side_effect=g.Stop('NOT_DISABLED')):
            with self.assertRaises(g.Stop):g.revoke_gate_after_verified_db()
            self.assertTrue(gate.exists())
    def test_25_gate_revoke_denied_without_journal(self):
        gate=self.path('gate');gate.write_bytes(g.GATE_BYTES);gate.chmod(0o600)
        with mock.patch.multiple(g,GATE=gate,DISABLE_ATTEMPT=self.path('absent'),GATE_REVOKED=self.path('rec')):
            with self.assertRaises(g.Stop):g.revoke_gate_after_verified_db()
            self.assertTrue(gate.exists())
    def test_26_gate_revoke_denied_untrusted_gate(self):
        gate=self.path('gate');gate.write_bytes(b'other');gate.chmod(0o600)
        attempt=self.path('attempt');attempt.write_text('{}');attempt.chmod(0o600)
        with mock.patch.multiple(g,GATE=gate,DISABLE_ATTEMPT=attempt,GATE_REVOKED=self.path('receipt')), \
             mock.patch.object(g,'confirmed_disabled',return_value={'dispatch_enabled':False}):
            with self.assertRaises(g.Stop):g.revoke_gate_after_verified_db()
            self.assertTrue(gate.exists())
    def test_27_private_file_rejects_hardlink(self):
        p=self.path('file');p.write_bytes(b'data');os.link(p,self.path('other'))
        self.assertFalse(g.regular_owned(p))
    def test_28_private_file_accepts_0600(self):
        p=self.path('file');p.write_bytes(b'data');p.chmod(0o600)
        self.assertTrue(g.regular_owned(p))

class TransactionAndSwitchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
    def fake_agent(self,committed=True):
        class Cursor:
            def __init__(self):self.last=None;self.statements=[]
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def execute(self,sql,params=None):
                self.last=(sql,params);self.statements.append(self.last)
                if 'cg_set_managed_dispatch' in sql and not committed:
                    raise RuntimeError('unknown_commit')
            def fetchone(self):
                sql=self.last[0]
                if 'session_user' in sql:return {'actor':'bridge_chatgpt'}
                if 'cg_set_managed_dispatch' in sql:
                    return {'r':{'task_id':g.PILOT,'dispatch_enabled':False,'changed':True,'policy_version':2}}
                if 'cg_managed_detail' in sql:
                    return {'r':{'dispatch_enabled':False,'policy_version':2,'version':2,'last_seq':2}}
                return {'r':None}
        class Conn:
            def __init__(self):self.cur=Cursor();self.committed=False
            def __enter__(self):return self
            def __exit__(self,et,ev,tb):self.committed=et is None
            def cursor(self):return self.cur
        c=Conn()
        return mock.Mock(_connect=mock.Mock(return_value=c)),c
    def test_29_transaction_writes_once_and_journals(self):
        actor,c=self.fake_agent()
        with mock.patch.object(g,'pre_disable'),mock.patch.object(g,'actors',return_value=(actor,None)), \
             mock.patch.object(g,'revoke_gate_after_verified_db'), \
             mock.patch.object(g,'DISABLE_ATTEMPT',self.root/'attempt'), \
             mock.patch.object(g,'DISABLE_RECEIPT',self.root/'receipt'):
            g.disable_once()
            self.assertTrue(c.committed)
            a=[x for x in c.cur.statements if 'cg_set_managed_dispatch' in x[0]]
            self.assertEqual(len(a),1)
            self.assertEqual(a[0][1],(g.PILOT,False,1,2))
            self.assertTrue((self.root/'attempt').exists())
            self.assertTrue((self.root/'receipt').exists())
    def test_30_unknown_commit_journal_forbids_repeat(self):
        actor,c=self.fake_agent(committed=False)
        with mock.patch.object(g,'pre_disable'),mock.patch.object(g,'actors',return_value=(actor,None)), \
             mock.patch.object(g,'revoke_gate_after_verified_db') as revoke, \
             mock.patch.object(g,'DISABLE_ATTEMPT',self.root/'attempt'), \
             mock.patch.object(g,'DISABLE_RECEIPT',self.root/'receipt'):
            with self.assertRaises(RuntimeError):g.disable_once()
            self.assertFalse(c.committed)
            self.assertTrue((self.root/'attempt').exists())
            self.assertFalse((self.root/'receipt').exists())
            revoke.assert_not_called()
            with self.assertRaises(g.Stop):g.disable_once()
    def test_31_verify_quiesced_rejects_gate(self):
        with mock.patch.object(g,'confirmed_disabled'), mock.patch.object(g,'gate_absent',side_effect=g.Stop('GATE')):
            with self.assertRaises(g.Stop):g.verify_quiesced()
    def test_32_verify_quiesced_checks_config_if_requested(self):
        p=self.root/'attempt';p.write_text('{}');p.chmod(0o600)
        q=self.root/'revoked';q.write_text('{}');q.chmod(0o600)
        with mock.patch.multiple(g,DISABLE_ATTEMPT=p,GATE_REVOKED=q), \
             mock.patch.object(g,'confirmed_disabled'), mock.patch.object(g,'gate_absent'), \
             mock.patch.object(g,'exact_configs',return_value=(b'8',b'7')) as check:
            g.verify_quiesced();check.assert_called_once()
    def test_33_worker_config_change_only_after_no_processes(self):
        cfg=self.root/'live';cfg.write_bytes(b'8');cfg.chmod(0o600)
        receipt=self.root/'receipt'
        with mock.patch.multiple(g,CFG=cfg,HANDOFF_RECEIPT=receipt,WAIT_SECONDS=15,STABLE_SECONDS=0), \
             mock.patch.object(g,'own_context'),mock.patch.object(g,'live_services'), \
             mock.patch.object(g,'verify_quiesced'),mock.patch.object(g,'prepared',return_value=(b'8',b'7')), \
             mock.patch.object(g,'pilot_state_safe',return_value=('s','c')), \
             mock.patch.object(g,'safe_processes',return_value=([],[])), \
             mock.patch.object(g,'time') as t, mock.patch.object(g,'SHA7',g.digest(b'7')):
            t.monotonic.side_effect=[0,0,0,2,2]
            t.sleep.return_value=None
            g.worker()
            self.assertEqual(cfg.read_bytes(),b'7')
            self.assertEqual(json.loads(receipt.read_text())['outcome'],'committed')
    def test_34_worker_timeout_retains_config(self):
        cfg=self.root/'live';cfg.write_bytes(b'8')
        receipt=self.root/'receipt'
        with mock.patch.multiple(g,CFG=cfg,HANDOFF_RECEIPT=receipt,WAIT_SECONDS=0), \
             mock.patch.object(g,'own_context'),mock.patch.object(g,'live_services'), \
             mock.patch.object(g,'verify_quiesced'),mock.patch.object(g,'prepared',return_value=(b'8',b'7')):
            with self.assertRaises(g.Stop):g.worker()
            self.assertEqual(cfg.read_bytes(),b'8')
            self.assertEqual(json.loads(receipt.read_text())['outcome'],'blocked')
    def test_35_worker_no_duplicate_handoff(self):
        p=self.root/'receipt';p.write_text('{}')
        with mock.patch.multiple(g,HANDOFF_RECEIPT=p),mock.patch.object(g,'own_context'), \
             mock.patch.object(g,'live_services'),mock.patch.object(g,'verify_quiesced'), \
             mock.patch.object(g,'prepared',return_value=(b'8',b'7')):
            with self.assertRaises(g.Stop):g.worker()
    def test_36_process_scanner_returns_tuple(self):
        a,b=g.safe_processes()
        self.assertIsInstance(a,list);self.assertIsInstance(b,list)
    def test_37_only_one_explicit_pilot(self):self.assertEqual(g.PILOT,'task_bddaadef89b7458fb7f8cf437dc0f023')
    def test_39_inspect_policy_is_read_only(self):
        with mock.patch.object(g,'own_context'),mock.patch.object(g,'live_services'), \
             mock.patch.object(g,'read_pg',return_value={'dispatch_enabled':False,
                        'policy_version':2,'version':2,'last_seq':2}), \
             mock.patch.object(g,'actors') as actors:
            g.inspect_policy()
            actors.assert_not_called()
    def test_40_unknown_policy_is_not_assumed_disabled(self):
        with mock.patch.object(g,'own_context'),mock.patch.object(g,'live_services'), \
             mock.patch.object(g,'read_pg',return_value={'dispatch_enabled':True,
                        'policy_version':1,'version':2,'last_seq':2}), \
             mock.patch.object(g,'revoke_gate_after_verified_db') as revoke:
            g.inspect_policy()
            revoke.assert_not_called()
    def test_38_previous_receipt_fingerprint(self):
        self.assertEqual(g.SHA8,'4b32ac0fe83c902f5a7e2e5a749b104506987e1380aadc863ff15ca57b7786c3')

if __name__=='__main__':unittest.main()
