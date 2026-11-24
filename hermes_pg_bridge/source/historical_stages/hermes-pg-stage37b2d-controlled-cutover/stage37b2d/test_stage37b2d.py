import hashlib
import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SPEC=importlib.util.spec_from_file_location('stage37b2d',Path(__file__).with_name('stage37b2d.py'))
m=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(m)

class ConfigTests(unittest.TestCase):
    def make(self):
        opts={'dry_run':False,'task_ids':[m.TASK],'tick_ms':30000}
        return {'plugin': [[str(m.CANARY),{'dry_run':False,'task_ids':['task_f2c64cfdf7894537b35a19a6def977c6']}]]+
                          [f'/home/kali/other-{i}.mjs' for i in range(1,7)]+
                          [[str(m.OLD),opts]],
                'plugins_misc':'preserve','env':{'abc':123},'model':'test'}
    def encoded(self):return (json.dumps(self.make(),sort_keys=True,separators=(',',':'))+'\n').encode()
    def test_one_path_exact_change(self):
        old=self.encoded();new=m.build_draft(old)
        self.assertEqual(json.loads(old)['plugin'][:7],json.loads(new)['plugin'][:7])
        self.assertEqual(json.loads(new)['plugin'][7][0],str(m.NEW))
        self.assertEqual(new.replace(json.dumps(str(m.NEW)).encode(),json.dumps(str(m.OLD)).encode()),old)
    def test_only_target_changes_not_other_metadata(self):
        orig=json.loads(self.encoded());n=json.loads(m.build_draft(self.encoded()))
        self.assertEqual({k:v for k,v in orig.items() if k!='plugin'},
                         {k:v for k,v in n.items() if k!='plugin'})
        self.assertEqual(orig['plugin'][7][1],n['plugin'][7][1])
    def test_reject_7_plugins(self):
        a=self.make();a['plugin'].pop();self.assertRaises(m.Stop,m.build_draft,json.dumps(a).encode())
    def test_reject_other_task_id(self):
        a=self.make();a['plugin'][-1][1]['task_ids']=['task_'+'0'*32];self.assertRaises(m.Stop,m.build_draft,json.dumps(a).encode())
    def test_reject_dry_run_change(self):
        a=self.make();a['plugin'][-1][1]['dry_run']=True;self.assertRaises(m.Stop,m.build_draft,json.dumps(a).encode())
    def test_reject_other_tick(self):
        a=self.make();a['plugin'][-1][1]['tick_ms']=1000;self.assertRaises(m.Stop,m.build_draft,json.dumps(a).encode())
    def test_reject_duplicate_plugin(self):
        a=self.make();a['plugin'][1]=a['plugin'][2];self.assertRaises(m.Stop,m.build_draft,json.dumps(a).encode())
    def test_reject_already_switched(self):
        a=self.make();a['plugin'][-1][0]=str(m.NEW);self.assertRaises(m.Stop,m.build_draft,json.dumps(a).encode())
    def test_reject_old_not_last(self):
        a=self.make();a['plugin'][-1],a['plugin'][-2]=a['plugin'][-2],a['plugin'][-1]
        self.assertRaises(m.Stop,m.build_draft,json.dumps(a).encode())
    def test_reject_missing_canary(self):
        a=self.make();a['plugin'][0][0]='other';self.assertRaises(m.Stop,m.build_draft,json.dumps(a).encode())
    def test_refuse_bad_schema(self):
        a=self.make();a['plugin'][2]=['x',{'a':1},'z'];self.assertRaises(m.Stop,m.build_draft,json.dumps(a).encode())
    def test_sha_helpers(self):
        self.assertEqual(m.digest(b'a'),hashlib.sha256(b'a').hexdigest())

class StateMachineTests(ConfigTests):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='stage37d-unit-')
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.patches=[]
        for key,val in {'ROOT':root/'install','CFG':root/'config.json','BACKUP':root/'install/backup.json',
          'DRAFT':root/'install/draft.json','PREP':root/'install/prepared.json',
          'ATTEMPT':root/'install/commit-attempt.json','COMMITTED':root/'install/committed.json',
          'VERIFIED':root/'install/verified.json','ROLLBACK_ATTEMPT':root/'install/rollback-attempt.json',
          'ROLLED_BACK':root/'install/rolled-back.json','LOCK':root/'install/lock',
          'PERMIT':root/'cap-permit','GATE':root/'cap-gate'}.items():
            patch=mock.patch.object(m,key,val);patch.start();self.addCleanup(patch.stop)
        m.ROOT.mkdir(mode=0o700)
        self.initial=self.encoded()
        self.wfile(m.CFG,self.initial)
        class B1:
            LOCK=root/'nonexistent-lock'
            def multitool_processes(self):return ([(999,1)],0)
        self.b1=B1()
        self.startmock('checked_config_meta',lambda:(self.initial,m.digest(self.initial)))
        self.startmock('verify_candidate',lambda:'a'*64)
        self.startmock('preflight_runtime',lambda:self.b1)
        self.startmock('processes_stopped',lambda b:None)
    def startmock(self,name,func):
        p=mock.patch.object(m,name,func);p.start();self.addCleanup(p.stop)
    def wfile(self,p,b):
        p.write_bytes(b);p.chmod(0o600)
    def test_prepare_does_not_change_active_config(self):
        m.prepare();self.assertEqual(m.CFG.read_bytes(),self.initial)
        self.assertTrue(m.DRAFT.exists());self.assertTrue(m.BACKUP.exists())
        self.assertFalse(m.ATTEMPT.exists())
    def test_prepared_same_bytes_and_strict_metadata(self):
        m.prepare();meta,old,draft=m.pinned_meta()
        self.assertEqual(old,self.initial)
        self.assertEqual(meta['new_config_sha'],m.digest(draft))
    def test_prepare_one_shot(self):
        m.prepare();self.assertRaises(m.Stop,m.prepare)
    def test_commit_changes_only_config(self):
        m.prepare();m.commit()
        self.assertTrue(m.COMMITTED.exists())
        self.assertEqual(m.CFG.read_bytes(),m.DRAFT.read_bytes())
        self.assertFalse(m.PERMIT.exists());self.assertFalse(m.GATE.exists())
    def test_commit_one_shot_and_preserved_backup(self):
        m.prepare();m.commit();self.assertRaises(m.Stop,m.commit)
        self.assertEqual(m.BACKUP.read_bytes(),self.initial)
    def test_verify_requires_running_process(self):
        m.prepare();m.commit()
        self.b1.multitool_processes=lambda: ([],0)
        self.assertRaises(m.Stop,m.verify)
    def test_verify_success_is_not_claim_import_verified(self):
        m.prepare();m.commit();m.verify();self.assertTrue(m.VERIFIED.exists())
    def test_rollback_restores_original_bytes(self):
        m.prepare();m.commit();m.rollback()
        self.assertEqual(m.CFG.read_bytes(),self.initial)
        self.assertTrue(m.ROLLED_BACK.exists())
    def test_rollback_one_shot(self):
        m.prepare();m.commit();m.rollback();self.assertRaises(m.Stop,m.rollback)
    def test_rollback_after_commit_journal_but_before_config_swap(self):
        m.prepare();m.create_exclusive(m.ATTEMPT,m.json_data({'phase':'test'}))
        m.rollback();self.assertEqual(m.CFG.read_bytes(),self.initial)
    def test_rollback_refuses_config_drift(self):
        m.prepare();m.commit();self.wfile(m.CFG,b'{}')
        self.assertRaises(m.Stop,m.rollback)
        self.assertFalse(m.ROLLBACK_ATTEMPT.exists())
    def test_commit_refuses_gate(self):
        m.prepare();self.wfile(m.GATE,b'bad')
        self.assertRaises(m.Stop,m.commit)
        self.assertFalse(m.ATTEMPT.exists())
    def test_atomic_replace_refuses_stale_sha(self):
        self.assertRaises(m.Stop,m.atomic_replace_config,'0'*64,b'new')
    def test_atomically_replace_config(self):
        m.atomic_replace_config(m.digest(self.initial),b'{"ok":true}')
        self.assertEqual(m.CFG.read_bytes(),b'{"ok":true}')
    def test_private_file_refuses_symlink(self):
        a=m.ROOT/'target';self.wfile(a,b'abc');b=m.ROOT/'link';b.symlink_to(a)
        self.assertRaises(m.Stop,m.trusted_bytes,b)
    def test_private_file_refuses_world_permissions(self):
        a=m.ROOT/'test';self.wfile(a,b'abc');a.chmod(0o644)
        self.assertRaises(m.Stop,m.trusted_bytes,a)
    def test_meta_tamper_stops(self):
        m.prepare();self.wfile(m.DRAFT,b'bad')
        self.assertRaises(m.Stop,m.pinned_meta)
    def test_journal_nonempty_stops(self):
        with mock.patch.object(m,'JOURNAL_DIR',m.ROOT/'intents'):
            m.JOURNAL_DIR.mkdir(mode=0o700)
            self.wfile(m.JOURNAL_DIR/'test',b'uncertain')
            self.assertRaises(m.Stop,m.journal_clean)
    def test_journal_missing_is_clean(self):
        with mock.patch.object(m,'JOURNAL_DIR',m.ROOT/'missing'):
            self.assertTrue(m.journal_clean())

if __name__=='__main__':unittest.main()
