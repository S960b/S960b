from __future__ import annotations
import ast
from pathlib import Path
import re
import unittest
from unittest.mock import patch
import registry34a as a

ROOT=Path(__file__).parent
SQL=(ROOT/'sql/006_managed_registry.sql').read_text()
BACK=(ROOT/'sql/007_rollback_if_empty.sql').read_text()

class Stage34ASecurityTests(unittest.TestCase):
    def test_schema_one_extra_registry_table(self):
        self.assertEqual(len(re.findall(r'CREATE TABLE bridge\.',SQL)),1)
        self.assertIn('CREATE TABLE bridge.managed_dialogs',SQL)
        self.assertIn('task_id text PRIMARY KEY REFERENCES bridge.dialogs(task_id)',SQL)
    def test_dispatch_is_disabled_by_default(self):
        self.assertIn('dispatch_enabled boolean NOT NULL DEFAULT false',SQL)
        self.assertIn('IF (SELECT count(*) FROM bridge.managed_dialogs)<>0',SQL)
    def test_no_automatic_legacy_enrollment(self):
        self.assertNotIn('INSERT INTO bridge.managed_dialogs SELECT',SQL.upper())
        self.assertIn('unmanaged_request_id_collision',SQL)
        self.assertIn("IF v->>'created'='true' THEN",SQL)
    def test_exactly_five_actor_wrappers(self):
        funcs=re.findall(r'CREATE FUNCTION (bridge\.[a-z_]+)\(',SQL)
        self.assertEqual(funcs,['bridge.cg_create_managed_dialog','bridge.cg_managed_status',
                                'bridge.ag_dispatch_list','bridge.ag_claim_managed_message',
                                'bridge.ag_send_managed_report'])
    def test_every_function_has_session_identity_guard(self):
        self.assertEqual(SQL.count("IF session_user::text<>"),5)
        self.assertEqual(SQL.count("USING ERRCODE='42501'"),7)
    def test_all_new_functions_security_definer(self):
        self.assertEqual(SQL.count('SECURITY DEFINER SET search_path=pg_catalog,bridge,pg_temp'),5)
    def test_agent_visibility_only_active_approved(self):
        fn=SQL.split('CREATE FUNCTION bridge.ag_dispatch_list',1)[1].split('CREATE FUNCTION bridge.ag_claim_managed_message',1)[0]
        self.assertIn("r.dispatch_enabled AND d.state='active' AND d.next_actor='agent'",fn)
        self.assertNotIn('bridge.list_dialogs(',fn)
        self.assertIn('LIMIT p_limit OFFSET p_offset',fn)
    def test_managed_claim_requires_enabled_and_locks(self):
        fn=SQL.split('CREATE FUNCTION bridge.ag_claim_managed_message',1)[1].split('CREATE FUNCTION bridge.ag_send_managed_report',1)[0]
        self.assertIn("r.dispatch_enabled AND d.state='active'",fn)
        self.assertIn('FOR SHARE OF r',fn)
        self.assertIn('bridge.ag_claim_message(',fn)
        self.assertIn('managed_dialog_not_enabled',fn)
    def test_disabled_inflight_may_report(self):
        fn=SQL.split('CREATE FUNCTION bridge.ag_send_managed_report',1)[1].split('RESET ROLE',1)[0]
        self.assertIn('unregistered_dialog',fn)
        self.assertIn("bridge.ag_send_message(p_task_id,p_message_id,'report'",fn)
        self.assertNotIn('dispatch_enabled',fn)
    def test_no_public_or_cross_actor_execute(self):
        self.assertIn('FROM PUBLIC,bridge_app,bridge_chatgpt,bridge_agent',SQL)
        self.assertIn('TO bridge_chatgpt',SQL)
        self.assertIn('TO bridge_agent',SQL)
        self.assertIn('STAGE34A_ROLE_CROSSOVER',SQL)
    def test_strict_migration_preflight_and_commit(self):
        self.assertIn("n<>25",SQL)
        self.assertIn("n<>3",SQL)
        self.assertIn("n<>30",SQL)
        self.assertIn("seq=5",SQL)
        self.assertIn("seq=6",SQL)
        self.assertTrue(SQL.lstrip().startswith('\\set ON_ERROR_STOP on'))
        self.assertEqual(SQL.count('BEGIN;'),1)
        self.assertEqual(SQL.count('COMMIT;'),1)
    def test_backout_refuses_registered_dialogs(self):
        self.assertIn('STAGE34A_ROLLBACK_REFUSED_MANAGED_DIALOGS_EXIST',BACK)
        self.assertEqual(BACK.count('DROP FUNCTION bridge.'),5)
        self.assertIn('DROP TABLE bridge.managed_dialogs',BACK)
        self.assertNotIn('DROP TABLE bridge.messages',BACK)
    def test_plugin_entry_schema_one_pin(self):
        original=[['/x/mt-test-plugin.mjs',{}],[str(a.PLUGIN),{'dry_run':False,'task_ids':[a.CANARY]}]]
        for j in range(5):original.append(f'/x/other{j}.mjs')
        a.plugin_entries({'plugin':original})
        altered=[*original]
        altered[1]=[str(a.PLUGIN),{'dry_run':False,'task_ids':[]}]
        with self.assertRaises(a.Halt):a.plugin_entries({'plugin':altered})
    def test_plugin_entry_rejects_git_plugin(self):
        original=[['/x/mt-git-inbox.mjs',{}],[str(a.PLUGIN),{'dry_run':False,'task_ids':[a.CANARY]}]]
        original.extend(f'/x/other{j}.mjs' for j in range(5))
        with self.assertRaises(a.Halt):a.plugin_entries({'plugin':original})
    def test_live_proof_does_explicit_rollback(self):
        src=Path(a.__file__).read_text()
        fn=src.split('def rollback_only_live_positive',1)[1].split('def readonly_only',1)[0]
        self.assertIn('c.rollback()',fn)
        self.assertIn('cg.creation_status(request) is None',fn)
        self.assertIn('created',fn)
    def test_no_arbitrary_command_exec(self):
        tree=ast.parse(Path(a.__file__).read_text())
        names={n.id for n in ast.walk(tree) if isinstance(n,ast.Name)}
        self.assertFalse({'subprocess','eval','exec'}&names)
    def test_scripts_use_private_authorized_ssh_and_no_sdk(self):
        src='\n'.join(x.read_text() for x in ROOT.glob('*.sh'))
        self.assertIn('StrictHostKeyChecking=yes',src)
        self.assertIn('PG_V2_SSH_TARGET',src)
        self.assertNotIn('systemctl --user restart',src)
        self.assertNotIn('opencode.json >',src)
    def test_migration_no_legacy_modify_or_dispatch_enable(self):
        self.assertNotIn('UPDATE bridge.dialogs SET',SQL)
        self.assertNotIn('UPDATE bridge.outbox SET',SQL)
        self.assertNotIn('UPDATE bridge.managed_dialogs SET dispatch_enabled',SQL)
        self.assertNotIn('GRANT SELECT ON bridge.managed_dialogs',SQL)

if __name__=='__main__': unittest.main()
