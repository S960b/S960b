"""Offline tests: no PostgreSQL access required."""
import ast
import json
import pathlib
import re
import unittest
from unittest.mock import patch
import pilot34f as mod

class Stage34FTests(unittest.TestCase):
    def test_request_id_stable(self):
        self.assertRegex(mod.REQUEST_ID, r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
    def test_title_bounded(self):
        self.assertLessEqual(len(mod.TITLE), 4096)
    def test_pilot_distinct_from_canary(self):
        self.assertNotEqual(mod.CANARY, 'task_' + 'a'*32)
    def test_hashes_complete(self):
        for digest in [mod.CONFIG_SHA,mod.ACTIVE_SHA,mod.PILOT_SHA]: self.assertRegex(digest,r'^[0-9a-f]{64}$')
    def test_port_15433_in_import_adapter(self):
        self.assertEqual(mod.ROLE_IMPORT,'/home/kali/hermes-pg-stage33e-shadow-clients')
    def test_no_write_gate_allowed(self):
        src=pathlib.Path(mod.__file__).read_text()
        self.assertIn("'MANAGED_WRITE_GATE_PRESENT'",src)
    def test_create_only_managed_sql(self):
        src=pathlib.Path(mod.__file__).read_text()
        self.assertEqual(src.count('SELECT bridge.cg_create_managed_dialog('),1)
        for forbidden in ('bridge.cg_send_managed_task(', 'bridge.cg_set_managed_dispatch(',
                          'bridge.ag_claim_managed_message(', 'bridge.ag_send_managed_report(',
                          'bridge.ag_ack_managed_message('):
            self.assertNotIn(forbidden,src)
    def test_created_defaults_disabled(self):
        self.assertIn("detail.get('policy_version') == 0", pathlib.Path(mod.__file__).read_text())
    def test_creation_status_query_for_reconciliation(self):
        self.assertIn('cg.creation_status(REQUEST_ID)', pathlib.Path(mod.__file__).read_text())
    def test_only_expected_namespace(self):
        self.assertTrue(mod.REQUEST_ID.startswith('stage34f_pilot_'))
    def test_no_automatic_retry(self):
        src=pathlib.Path(mod.__file__).read_text()
        self.assertIn('write_atomic_no_replace(ATTEMPT',src)
        self.assertLess(src.index('write_atomic_no_replace(ATTEMPT'),src.index('SELECT bridge.cg_create_managed_dialog('))
    def test_schema_guard(self):
        src=pathlib.Path(mod.__file__).read_text()
        self.assertIn("'FUNCTION_COUNT_NOT_42'",src)
        self.assertIn("'TABLE_COUNT_NOT_4'",src)
    def test_agent_dispatch_empty(self):
        self.assertIn('MANAGED_DISPATCH_NOT_EMPTY',pathlib.Path(mod.__file__).read_text())
    def test_receipt_not_select_from_tables(self):
        src=pathlib.Path(mod.__file__).read_text()
        self.assertNotRegex(src, r'\bFROM\s+bridge\.(dialogs|outbox|messages|managed_dialogs)\b')
    def test_binding_candidate_pinned(self):
        tid='task_'+'a'*32
        binding=mod.expected_binding(tid)
        self.assertEqual(binding['task_id'],tid)
        self.assertEqual(binding['candidate_sha256'],mod.PILOT_SHA)
        self.assertIs(binding['dispatch_enabled'],False)
        self.assertIs(binding['sdk_enabled'],False)
        self.assertIs(binding['write_gate_armed'],False)
    def test_reject_bad_task_id(self):
        class Fake:
            def _connect(self): raise AssertionError('must reject before DB')
        for tid in ('invalid','task_'+'g'*32,mod.CANARY):
            with self.assertRaises(mod.Refused): mod.creation_receipt(Fake(),tid)
    def test_readonly_status_flags(self):
        src=pathlib.Path(mod.__file__).read_text()
        self.assertIn('STAGE34F_VERIFY_ONLY_PILOT_NOT_COMMITTED',src)
        self.assertIn('STAGE34F_ONE_DISABLED_MANAGED_DIALOG_VERIFIED',src)
    def test_cli_only_three_modes(self):
        src=pathlib.Path(mod.__file__).read_text()
        self.assertIn("('preflight','create-once','verify-only')",src)
    def test_no_service_mutation(self):
        src=pathlib.Path(mod.__file__).read_text()
        for marker in ('systemctl','os.replace(','os.system(','subprocess.run(','sdk_session'):
            self.assertNotIn(marker,src)

if __name__=='__main__': unittest.main()
