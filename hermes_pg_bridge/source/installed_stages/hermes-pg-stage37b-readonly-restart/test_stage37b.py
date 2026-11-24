import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import stage37b as s

class RestartReadOnlyTests(unittest.TestCase):
    def test_sha_repeatable(self):
        self.assertEqual(s.digest(b'a'),s.digest(b'a'))
        self.assertNotEqual(s.digest(b'a'),s.digest(b'b'))

    def test_process_stat_parses_space_in_comm(self):
        vals=['S']+['0']*50
        vals[19]='123456789'
        self.assertEqual(s.process_start_time(('123 (Multi Tool testing) '+ ' '.join(vals)).encode()),123456789)

    def test_process_stat_bad(self):
        for raw in [b'',b'1 (comm) S 0',b'1 comm S 0',b'1 (x) S '+b'a '*30]:
            with self.subTest(raw=raw),self.assertRaises(s.Stop):s.process_start_time(raw)

    def test_check_process_start_field_22(self):
        v=['S']+[str(n) for n in range(2,55)]
        self.assertEqual(s.process_start_time(('99 (a) '+ ' '.join(v)).encode()),20)

    def test_stat_private_file(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'x'
            p.write_bytes(b'valid')
            os.chmod(p,0o600)
            self.assertEqual(s.private_file_bytes(p),b'valid')

    def test_public_file_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'x'
            p.write_bytes(b'valid')
            os.chmod(p,0o644)
            with self.assertRaises(s.Stop):s.private_file_bytes(p)

    def test_symlink_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'a'; q=Path(d)/'b'
            p.write_bytes(b'valid');q.symlink_to(p)
            with self.assertRaises(s.Stop):s.private_file_bytes(q)

    def test_limit_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'a';p.write_bytes(b'1234');os.chmod(p,0o600)
            with self.assertRaises(s.Stop):s.private_file_bytes(p,3)

    def test_trusted_dir_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'test';p.mkdir(mode=0o700)
            self.assertTrue(s.trusted_dir(p))
            os.chmod(p,0o777)
            self.assertFalse(s.trusted_dir(p))

    def test_absent_permits_ok(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(s,'PERMIT',Path(d)/'p'), patch.object(s,'GATE',Path(d)/'g'):
                s.absent_active_capabilities()

    def test_present_permit_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'permit';p.write_text('{}')
            with patch.object(s,'PERMIT',p), patch.object(s,'GATE',Path(d)/'g'):
                with self.assertRaises(s.Stop):s.absent_active_capabilities()

    def test_symlink_gate_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'gate';p.symlink_to('/tmp/nowhere')
            with patch.object(s,'PERMIT',Path(d)/'p'), patch.object(s,'GATE',p):
                with self.assertRaises(s.Stop):s.absent_active_capabilities()

    def test_compare_equal(self):
        a={'config_sha256':'a','state':{'seen':1},'db':{'version':2}}
        s.compare(a,a.copy())

    def test_compare_config_drift(self):
        a={'config_sha256':'a','state':{},'db':{}}
        with self.assertRaises(s.Stop):s.compare({'config_sha256':'b','state':{},'db':{}},a)

    def test_compare_cursor_drift(self):
        a={'config_sha256':'a','state':{'seen':1},'db':{}}
        with self.assertRaises(s.Stop):s.compare({'config_sha256':'a','state':{'seen':2},'db':{}},a)

    def test_compare_db_drift(self):
        a={'config_sha256':'a','state':{},'db':{'version':2}}
        with self.assertRaises(s.Stop):s.compare({'config_sha256':'a','state':{},'db':{'version':3}},a)

    def test_create_once_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'baseline.json'
            with patch.object(s,'ROOT',Path(d)):
                s.create_once(p,{'phase':'pre_restart'})
                self.assertEqual(s.read_marker(p)['phase'],'pre_restart')
                self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o600)
                with self.assertRaises(FileExistsError):s.create_once(p,{'phase':'changed'})

    def test_locked_file_private(self):
        with tempfile.TemporaryDirectory() as d:
            lock=Path(d)/'lock'
            with patch.object(s,'LOCK_FILE',lock):
                with s.mutex():self.assertTrue(lock.exists())
                self.assertEqual(stat.S_IMODE(lock.stat().st_mode),0o600)

    def test_state_seen_good(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state.json'
            p.write_text(json.dumps({'seen':{s.TASK:1},'inflight':{}}))
            os.chmod(p,0o600)
            with patch.object(s,'STATE',p):
                self.assertEqual(s.state_snapshot()['seen'],1)

    def test_state_inflight_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state.json'
            p.write_text(json.dumps({'seen':{s.TASK:1},'inflight':{s.TASK:{'seq':1}}}))
            os.chmod(p,0o600)
            with patch.object(s,'STATE',p):
                with self.assertRaises(s.Stop):s.state_snapshot()

    def test_state_missing_seen_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state.json'
            p.write_text(json.dumps({'seen':{},'inflight':{}}));os.chmod(p,0o600)
            with patch.object(s,'STATE',p):
                with self.assertRaises(s.Stop):s.state_snapshot()

    def test_state_bool_seen_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state.json'
            p.write_text(json.dumps({'seen':{s.TASK:True},'inflight':{}}));os.chmod(p,0o600)
            with patch.object(s,'STATE',p):
                with self.assertRaises(s.Stop):s.state_snapshot()

    def test_state_invalid_json_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state.json';p.write_text('invalid');os.chmod(p,0o600)
            with patch.object(s,'STATE',p):
                with self.assertRaises(ValueError):s.state_snapshot()

    def test_config_exact_scope_and_sha(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d)
            cfg=d/'opencode.json';prep=d/'prepared.json'
            entries=['/some/other/'+str(i) for i in range(6)]+[
                [str(s.PLUGIN),{'task_ids':[s.TASK],'dry_run':False,'tick_ms':30000}],
                [str(s.CANARY_JS),{'task_ids':[s.CANARY],'dry_run':False}]]
            raw=(json.dumps({'plugin':entries})+'\n').encode()
            cfg.write_bytes(raw);prep.write_text(json.dumps({'cfg8_sha':s.digest(raw)}))
            os.chmod(cfg,0o600);os.chmod(prep,0o600)
            with patch.object(s,'CFG',cfg),patch.object(s,'STAGE36B',prep):
                self.assertEqual(s.config_snapshot(),s.digest(raw))

    def test_config_rejects_scope_widening(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);cfg=d/'cfg';prep=d/'prep'
            entries=[['/other/'+str(i),{}] for i in range(6)]+[
                [str(s.PLUGIN),{'task_ids':[s.TASK,s.PILOT],'dry_run':False,'tick_ms':30000}],
                [str(s.CANARY_JS),{'task_ids':[s.CANARY],'dry_run':False}]]
            raw=json.dumps({'plugin':entries}).encode()
            cfg.write_bytes(raw);prep.write_text(json.dumps({'cfg8_sha':s.digest(raw)}))
            os.chmod(cfg,0o600);os.chmod(prep,0o600)
            with patch.object(s,'CFG',cfg),patch.object(s,'STAGE36B',prep):
                with self.assertRaises(s.Stop):s.config_snapshot()

    def test_quiet_prohibits_running_instances(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d)
            with patch.object(s,'ROOT',d),patch.object(s,'BASELINE',d/'baseline.json'),\
                 patch.object(s,'QUIESCED',d/'quiesced.json'),patch.object(s,'LOCK_FILE',d/'lock'),\
                 patch.object(s,'multitool_processes',return_value=([[5,123]],0)):
                (d/'baseline.json').write_text(json.dumps({'phase':'pre_restart'}));os.chmod(d/'baseline.json',0o600)
                with self.assertRaises(s.Stop):s.quiet()

    def test_before_rejects_second_baseline(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d)
            with patch.object(s,'ROOT',d),patch.object(s,'BASELINE',d/'baseline.json'),\
                 patch.object(s,'LOCK_FILE',d/'lock'):
                (d/'baseline.json').write_text('{}')
                with self.assertRaises(s.Stop):s.before()

    def test_post_requires_quiesced(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d)
            with patch.object(s,'ROOT',d),patch.object(s,'BASELINE',d/'baseline.json'),\
                 patch.object(s,'QUIESCED',d/'quiesced.json'),patch.object(s,'LOCK_FILE',d/'lock'):
                (d/'baseline.json').write_text('{}');os.chmod(d/'baseline.json',0o600)
                with self.assertRaises(FileNotFoundError):s.after()

    def test_pre_does_not_mutate_post_state(self):
        self.assertNotEqual(s.BASELINE,s.AFTER)
        self.assertNotEqual(s.QUIESCED,s.AFTER)

    def test_frozen_stage36_ids(self):
        self.assertEqual(s.TASK,'task_b6c26aa18220412bafdbf32393f16568')
        self.assertEqual(s.PILOT,'task_bddaadef89b7458fb7f8cf437dc0f023')

if __name__=='__main__':unittest.main()
