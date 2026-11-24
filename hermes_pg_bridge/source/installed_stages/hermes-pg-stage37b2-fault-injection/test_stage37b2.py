import hashlib
import pathlib
import unittest
from unittest.mock import patch
from stage37b2 import digest,check_source,CANDIDATE_SHA,private_file,Stop,need

class TestGuard(unittest.TestCase):
    def test_01_digest(self):self.assertEqual(digest(b'abc'),hashlib.sha256(b'abc').hexdigest())
    def test_02_sha_pin_len(self):self.assertEqual(len(CANDIDATE_SHA),64)
    def test_03_sha_hex(self):self.assertEqual(bytes.fromhex(CANDIDATE_SHA).hex(),CANDIDATE_SHA)
    def test_04_no_unknown_blob(self):self.assertFalse(check_source(b'') )
    def test_05_untrusted_text(self):self.assertFalse(check_source(b'x'*1000))
    def test_06_wrong_unicode(self):self.assertFalse(check_source(b'\xff'))
    def test_07_no_source_in_package(self):self.assertFalse(pathlib.Path('mt-pg-inbox-managed-stage34eb-pilot-unregistered.mjs').exists())
    def test_08_no_reflection_execution(self):self.assertNotIn('importlib.util.spec_from_file_location(\'plugin\'',pathlib.Path('stage37b2.py').read_text())
    def test_09_database_readonly(self):self.assertIn('b1.pg_snapshot()',pathlib.Path('stage37b2.py').read_text())
    def test_10_no_issue_permit(self):self.assertNotIn('managed-mcp-write-permit35c.json\',\'w\'',pathlib.Path('stage37b2.py').read_text())
    def test_11_no_agent_gate_issue(self):self.assertNotIn('ALLOW_MANAGED_WRITES_AFTER',pathlib.Path('stage37b2.py').read_text())
    def test_12_absent_capabilities(self):self.assertIn('b1.absent_active_capabilities()',pathlib.Path('stage37b2.py').read_text())
    def test_13_watchdog_active(self):self.assertIn('b1.systemd_timer_active()',pathlib.Path('stage37b2.py').read_text())
    def test_14_exact_sha(self):self.assertIn('digest(blob)!=CANDIDATE_SHA',pathlib.Path('stage37b2.py').read_text())
    def test_15_no_live_sdk(self):self.assertNotIn('session.prompt(',pathlib.Path('stage37b2.py').read_text())
    def test_16_no_process_restart(self):self.assertNotIn('systemctl restart',pathlib.Path('stage37b2.py').read_text())
    def test_17_no_config_edit(self):self.assertNotIn('write_text(',pathlib.Path('stage37b2.py').read_text())
    def test_18_no_db_write(self):self.assertNotIn('INSERT INTO',pathlib.Path('stage37b2.py').read_text())
    def test_19_no_direct_sql_execute(self):self.assertNotIn('cur.execute(',pathlib.Path('stage37b2.py').read_text())
    def test_20_require_private(self):self.assertIn('st.st_mode & 0o077',pathlib.Path('stage37b2.py').read_text())
    def test_21_guard_error(self):
        with self.assertRaises(Stop):need(False,'TEST_FAIL')
    def test_22_guard_ok(self):need(True,'TEST_OK')
    def test_23_no_symlink(self):self.assertIn('not CANDIDATE.is_symlink()',pathlib.Path('stage37b2.py').read_text())
    def test_24_no_os_delete(self):self.assertNotIn('unlink(',pathlib.Path('stage37b2.py').read_text())

if __name__=='__main__':unittest.main()
