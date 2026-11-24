"""Offline, mutation-free unit tests for Stage 34F-B config handoff."""
import hashlib
import json
import unittest
from unittest.mock import patch
import handoff34fb as h

BASE = {
    'model': 'example',
    'plugin': [
        '/home/kali/plugins/mt-test-plugin.mjs',
        ['/home/kali/plugins/linkedin-daily.mjs', {'run': 'daily'}],
        '/home/kali/plugins/news-mt-plugin.mjs',
        '/home/kali/plugins/news-p3-plugin.mjs',
        '/home/kali/plugins/news-p4-v2-plugin.mjs',
        '/home/kali/plugins/v21_plugin.mjs',
        [str(h.CANARY_SRC), {'dry_run': False, 'task_ids': [h.CANARY], 'tick_ms':30000}],
    ],
}

def raw(obj=None):
    return json.dumps(BASE if obj is None else obj, ensure_ascii=False).encode()

class ConfigTests(unittest.TestCase):
    def test_build_eight_entries(self):
        out = json.loads(h.build_config(raw()))
        self.assertEqual(len(out['plugin']), 8)
    def test_existing_entries_unchanged(self):
        out = json.loads(h.build_config(raw()))
        self.assertEqual(out['plugin'][:7], BASE['plugin'])
    def test_outside_plugin_unchanged(self):
        out = json.loads(h.build_config(raw()))
        self.assertEqual(out['model'], 'example')
    def test_new_pilot_only(self):
        out = json.loads(h.build_config(raw()))
        self.assertEqual(out['plugin'][-1][1]['task_ids'], [h.PILOT])
    def test_candidate_path_exact(self):
        out = json.loads(h.build_config(raw()))
        self.assertEqual(out['plugin'][-1][0], str(h.PILOT_SRC))
    def test_active_capable_explicit(self):
        out = json.loads(h.build_config(raw()))
        self.assertIs(out['plugin'][-1][1]['dry_run'], False)
    def test_task_ids_disjoint(self):
        out = json.loads(h.build_config(raw()))
        self.assertNotEqual(out['plugin'][-1][1]['task_ids'],out['plugin'][6][1]['task_ids'])
    def test_canary_retained(self):
        out = json.loads(h.build_config(raw()))
        self.assertEqual(out['plugin'][6][1]['task_ids'],[h.CANARY])
    def test_empty_entries_rejected(self):
        obj = json.loads(raw());obj['plugin'] = []
        with self.assertRaises(h.Stop):h.build_config(raw(obj))
    def test_wrong_seven_count_rejected(self):
        obj = json.loads(raw());obj['plugin'].pop(0)
        with self.assertRaises(h.Stop):h.build_config(raw(obj))
    def test_missing_canary_rejected(self):
        obj = json.loads(raw());obj['plugin'][6][0] = '/tmp/not-canary'
        with self.assertRaises(h.Stop):h.build_config(raw(obj))
    def test_duplicate_canary_rejected(self):
        obj = json.loads(raw());obj['plugin'][5] = obj['plugin'][6]
        with self.assertRaises(h.Stop):h.build_config(raw(obj))
    def test_canary_filter_drift_rejected(self):
        obj = json.loads(raw());obj['plugin'][6][1]['task_ids']=[h.PILOT]
        with self.assertRaises(h.Stop):h.build_config(raw(obj))
    def test_canary_dry_run_rejected(self):
        obj = json.loads(raw());obj['plugin'][6][1]['dry_run']=True
        with self.assertRaises(h.Stop):h.build_config(raw(obj))
    def test_already_registered_rejected(self):
        obj = json.loads(raw());obj['plugin'][0]=str(h.PILOT_SRC)
        with self.assertRaises(h.Stop):h.build_config(raw(obj))
    def test_git_plugin_rejected(self):
        obj = json.loads(raw());obj['plugin'][0]='/home/kali/mt-git-inbox.mjs'
        with self.assertRaises(h.Stop):h.build_config(raw(obj))
    def test_old_canary_plugin_rejected(self):
        obj = json.loads(raw());obj['plugin'][0]='/home/kali/mt-pg-inbox-canary.mjs'
        with self.assertRaises(h.Stop):h.build_config(raw(obj))
    def test_bad_plugin_shape_rejected(self):
        obj=json.loads(raw());obj['plugin'][1]={'name':'abc'}
        with self.assertRaises(h.Stop):h.build_config(raw(obj))
    def test_invalid_json_rejected(self):
        with self.assertRaises(json.JSONDecodeError):h.build_config(b'not-json')
    def test_output_deterministic(self):
        self.assertEqual(h.build_config(raw()),h.build_config(raw()))
    def test_pilot_identity_is_fixed_non_canary(self):
        self.assertRegex(h.PILOT,r'^task_[0-9a-f]{32}$')
        self.assertNotEqual(h.PILOT,h.CANARY)
    def test_pilot_registration_alone_does_not_enable_policy(self):
        out=h.build_config(raw()).decode()
        self.assertNotIn('dispatch_enabled',out)
    def test_no_git_script_or_arbitrary_task_in_handoff(self):
        from pathlib import Path
        src=Path(h.__file__).read_text()
        self.assertNotIn('subprocess.Popen',src)
        self.assertNotIn('os.kill(',src)
        self.assertNotIn('session.prompt(',src)
        self.assertNotIn('cg_set_managed_dispatch(',src)
    def test_file_integrity_helpers(self):
        self.assertEqual(h.sha_bytes(b'abc'),hashlib.sha256(b'abc').hexdigest())
    def test_service_guard_is_present(self):
        from pathlib import Path
        self.assertIn('services_up(); pg_guard()',Path(h.__file__).read_text())
    def test_no_db_writes_in_pg_guard(self):
        import inspect
        src=inspect.getsource(h.pg_guard)
        for forbidden in ('INSERT INTO','UPDATE bridge.', 'DELETE FROM','cg_set_managed_dispatch','cg_send_managed_task'):
            self.assertNotIn(forbidden,src)
        self.assertEqual(src.count('SET TRANSACTION READ ONLY'),2)
    def test_state_guard_has_canary(self):
        import inspect
        self.assertIn('CANARY_DURABLE_CURSOR_CHANGED',inspect.getsource(h.durable_state_guard))
    def test_no_config_change_during_prepare(self):
        import inspect
        self.assertIn('CONFIG_CHANGED_DURING_PREPARE',inspect.getsource(h.prepare))
    def test_exclusive_process_check_before_commit(self):
        import inspect
        src=inspect.getsource(h.worker)
        self.assertLess(src.index('MULTITOOL_REAPPEARED_AT_COMMIT'),src.index('atom_write(CFG,draft,replace=True)'))
    def test_rollback_is_separately_guarded(self):
        import inspect
        src=inspect.getsource(h.rollback)
        self.assertIn('ALL_MULTITOOL_PROCESSES_MUST_BE_CLOSED',src)
        self.assertIn('pg_guard()',src)

if __name__=='__main__':unittest.main()
