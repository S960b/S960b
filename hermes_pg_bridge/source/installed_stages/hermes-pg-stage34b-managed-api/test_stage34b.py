#!/usr/bin/env python3
"""Offline safety/contract tests. Positive PostgreSQL proof runs separately on Kali."""
import importlib.util
import pathlib
import re
import unittest

ROOT=pathlib.Path(__file__).resolve().parent
SQL=(ROOT/'sql/008_managed_api.sql').read_text()
PY=(ROOT/'stage34b.py').read_text()
MCP=(ROOT/'shadow_mcp34b.py').read_text()
BASH={x.name:x.read_text() for x in ROOT.glob('*.sh')}

class Stage34BTests(unittest.TestCase):
    def test_schema_migration_transactional(self):
        self.assertTrue(SQL.startswith('\\set ON_ERROR_STOP on'))
        self.assertIn('BEGIN;',SQL)
        self.assertIn('COMMIT;',SQL)
        self.assertEqual(SQL.count('CREATE FUNCTION bridge.'),9)
        self.assertIn("IF n<>30",SQL)
        self.assertIn("IF n<>39",SQL)
    def test_predicates_real_identity(self):
        self.assertEqual(SQL.count("session_user::text<>'bridge_chatgpt'"),7)
        self.assertEqual(SQL.count("session_user::text<>'bridge_agent'"),2)
        self.assertNotIn('CURRENT_USER',SQL)
    def test_function_names_are_unique(self):
        functions=re.findall(r'CREATE FUNCTION bridge\.([a-z_]+)\(',SQL)
        self.assertEqual(len(functions),9)
        self.assertEqual(len(set(functions)),9)
    def test_policy_has_optimistic_version(self):
        self.assertIn('ADD COLUMN policy_version bigint NOT NULL DEFAULT 0',SQL)
        self.assertIn('r.policy_version<>p_expected_policy_version',SQL)
        self.assertIn('d.version<>p_expected_dialog_version',SQL)
        self.assertIn('policy_version=policy_version+1',SQL)
    def test_unmanaged_rejected(self):
        self.assertIn("RAISE EXCEPTION 'not_managed'",SQL)
        self.assertIn("RAISE EXCEPTION 'managed_dispatch_disabled'",SQL)
    def test_sender_is_never_chosen_by_caller(self):
        self.assertIn("bridge.cg_send_message(p_task_id,p_message_id,'task',p_body,p_expected_version)",SQL)
        self.assertNotIn('p_sender',SQL)
    def test_report_delivery_is_claimed_only_by_chatgpt(self):
        self.assertIn("x.recipient='chatgpt' AND m.sender='agent' AND m.kind='report'",SQL)
        self.assertIn('FOR UPDATE OF x;',SQL)
        self.assertIn("status='leased',attempts=attempts+1",SQL)
    def test_agent_message_gates(self):
        self.assertIn("m.sender='chatgpt' AND m.recipient='agent'",SQL)
        self.assertIn("(r.dispatch_enabled OR o.status IN ('leased','delivered'))",SQL)
    def test_ack_guards(self):
        self.assertIn("o.recipient='agent' AND m.sender='chatgpt' AND m.kind='task'",SQL)
        self.assertIn("o.recipient='chatgpt' AND m.sender='agent' AND m.kind='report'",SQL)
        self.assertIn('RETURN bridge.ag_ack_outbox',SQL)
        self.assertIn('RETURN bridge.ack_outbox',SQL)
    def test_no_raw_table_grants(self):
        self.assertNotRegex(SQL,r'GRANT\s+(?:SELECT|INSERT|UPDATE|DELETE)\s+ON\s+TABLE')
        self.assertIn('REVOKE ALL ON FUNCTION',SQL)
        self.assertIn('FROM PUBLIC,bridge_app,bridge_chatgpt,bridge_agent;',SQL)
    def test_guard_role_order(self):
        self.assertIn("GRANT EXECUTE ON FUNCTION bridge.cg_list_managed_dialogs",SQL)
        self.assertIn('TO bridge_chatgpt;',SQL)
        self.assertIn('TO bridge_agent;',SQL)
        self.assertIn("has_function_privilege('bridge_app'",SQL)
    def test_proof_always_rolls_back(self):
        self.assertIn('finally:',PY)
        self.assertIn('conn.rollback()',PY)
        self.assertIn("cg.creation_status(request_id) is None",PY)
        self.assertIn('SAVEPOINT before_denied_send',PY)
    def test_preflight_pins_current_config(self):
        self.assertIn('from registry34a import pin_runtime, verify_canary',PY)
        self.assertIn("task_f2c64cfdf7894537b35a19a6def977c6",PY)
        self.assertIn("bridge.ag_dispatch_list(0,50)",PY)
    def test_pg_catalog_used_not_information_schema(self):
        self.assertIn('pg_catalog.pg_attribute',PY)
        self.assertNotIn('information_schema',PY)
    def test_shadow_mcp_no_write_tools(self):
        self.assertEqual(MCP.count('@app.tool('),1)
        self.assertIn('readOnlyHint=True',MCP)
        self.assertNotIn('send_once',MCP)
        self.assertNotIn('cg_create_managed_dialog',MCP)
    def test_shadow_loopback_only(self):
        self.assertIn("HOST='127.0.0.1'",MCP)
        self.assertIn('PORT=18775',MCP)
        self.assertIn('tunnel_attached',MCP)
        self.assertNotIn('CONTROL_PLANE_TUNNEL_ID',MCP)
    def test_install_safely_one_shot(self):
        self.assertIn('STAGE34B_STOP_DDL_OUTCOME_UNKNOWN_VERIFY_ONLY',BASH['68_apply_sql_once.sh'])
        self.assertIn('ON_ERROR_STOP=1',BASH['68_apply_sql_once.sh'])
        self.assertIn('stage34b.py proof',BASH['68_apply_sql_once.sh'])
    def test_readonly_verify_after_ddl(self):
        self.assertIn('stage34b.py verify',BASH['69_verify_only.sh'])
        self.assertNotIn('psql',BASH['69_verify_only.sh'])
    def test_unit_not_enabled_and_no_tunnel_cutover(self):
        self.assertIn('systemctl --user start hermes-pg-stage34b-shadow.service',BASH['70_install_readonly_shadow.sh'])
        self.assertNotIn('systemctl --user enable ',BASH['70_install_readonly_shadow.sh'])
        self.assertNotIn('hermes-pg-tunnel-stage33fd.service',BASH['70_install_readonly_shadow.sh'])

if __name__=='__main__':unittest.main(verbosity=2)
