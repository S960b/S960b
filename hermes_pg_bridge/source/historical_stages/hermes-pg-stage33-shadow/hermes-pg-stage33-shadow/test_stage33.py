#!/usr/bin/env python3
import json
from pathlib import Path
import re
import unittest
from verify33 import validate,EXPECTED_FALSE,EXPECTED_TRUE,EXPECTED_ZERO

SRC=Path(__file__).parent/'sql/004_shadow_roles.sql'
SQL=SRC.read_text()

class ShadowRolesTests(unittest.TestCase):
    def test_nine_actor_guarded_wrappers(self):
        functions=re.findall(r'CREATE FUNCTION bridge\.(\w+)\(',SQL)
        self.assertEqual(set(functions),{
            'cg_create_dialog','cg_send_message','cg_return_turn','cg_tombstone_dialog',
            'ag_send_message','ag_claim_outbox','ag_claim_message','ag_ack_outbox',
            'ag_release_outbox'})
        self.assertEqual(SQL.count('session_user::text <>'),9)
        self.assertEqual(len(re.findall(r'LANGUAGE plpgsql SECURITY DEFINER',SQL)),9)
        self.assertEqual(SQL.count('SET search_path=pg_catalog,bridge,pg_temp'),9)
    def test_sender_and_recipient_are_literals(self):
        self.assertIn("bridge.send_message(p_task_id,p_message_id,'chatgpt',p_kind",SQL)
        self.assertIn("bridge.send_message(p_task_id,p_message_id,'agent',p_kind",SQL)
        self.assertIn("bridge.create_dialog(p_request_id,p_title,'chatgpt')",SQL)
        self.assertIn("bridge.claim_outbox('agent',p_consumer",SQL)
        self.assertNotIn('p_sender text',SQL)
        self.assertNotIn('p_recipient text',SQL)
    def test_additive_without_legacy_revocation(self):
        self.assertEqual(SQL.count('CREATE ROLE bridge_'),2)
        self.assertIn('CREATE ROLE bridge_chatgpt NOLOGIN',SQL)
        self.assertIn('CREATE ROLE bridge_agent NOLOGIN',SQL)
        self.assertNotIn('DROP TABLE',SQL.upper())
        self.assertNotIn('TRUNCATE',SQL.upper())
        self.assertNotIn('ALTER ROLE bridge_app',SQL)
        self.assertNotIn('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA bridge FROM PUBLIC,bridge_app',SQL)
        self.assertNotIn('ALTER DATABASE',SQL.upper())
    def test_acls_deny_cross_actor(self):
        self.assertIn('TO bridge_chatgpt;',SQL)
        self.assertIn('TO bridge_agent;',SQL)
        self.assertIn('TO bridge_chatgpt,bridge_agent;',SQL)
        self.assertIn('STAGE33_CROSS_ACTOR_EXECUTE_LEAK',SQL)
    def test_all_edits_transaction_scoped(self):
        self.assertIn('BEGIN;',SQL)
        self.assertIn('COMMIT;',SQL)
        self.assertIn("lock_timeout = '5s'",SQL)
        self.assertIn('STAGE33_FUNCTION_PREFLIGHT_MISMATCH',SQL)
    def test_verification_success_and_failures(self):
        row={'database':'bridge','function_count':25,'dialog_count':3,'message_count':4}
        row.update({k:True for k in EXPECTED_TRUE})
        row.update({k:False for k in EXPECTED_FALSE})
        row.update({k:0 for k in EXPECTED_ZERO})
        self.assertEqual(validate(json.dumps(row)),row)
        for k in EXPECTED_TRUE:
            t=row.copy();t[k]=False
            with self.assertRaises(ValueError,msg=k):validate(json.dumps(t))
        for k in EXPECTED_FALSE:
            t=row.copy();t[k]=True
            with self.assertRaises(ValueError,msg=k):validate(json.dumps(t))
        for k in EXPECTED_ZERO:
            t=row.copy();t[k]=1
            with self.assertRaises(ValueError,msg=k):validate(json.dumps(t))
    def test_no_sensitive_output(self):
        s=(Path(__file__).parent/'verify33.py').read_text()
        self.assertNotIn('print(obj)',s)
        self.assertNotIn('print(raw)',s)
if __name__=='__main__':unittest.main(verbosity=2)
