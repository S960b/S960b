import pathlib
import re
import unittest
from unittest.mock import patch
import stage33b as m

HERE = pathlib.Path(__file__).resolve().parent

class TestStage33B(unittest.TestCase):
    def test_scram_prehash_format(self):
        v = m.scram_verifier('x'*64, bytes(range(16)))
        self.assertTrue(v.startswith('SCRAM-SHA-256$4096:'))
        self.assertEqual(v.count('$'),2)
        self.assertEqual(v.count(':'),2)
        self.assertRegex(v, r'^SCRAM-SHA-256\$4096:[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+$')

    def test_scram_vectors_stable_and_salted(self):
        a = m.scram_verifier('X'*64,b'0123456789abcdef')
        self.assertEqual(a,m.scram_verifier('X'*64,b'0123456789abcdef'))
        self.assertNotEqual(a,m.scram_verifier('X'*64,b'0123456789abcdeg'))

    def test_password_validation(self):
        for value in ('foo','X'*63,'X'*65,'! '*32):
            with self.assertRaises(ValueError):
                m.scram_verifier(value,b'0123456789abcdef')
        with self.assertRaises(ValueError):
            m.scram_verifier('X'*64,b'bad')

    def test_sql_only_two_logins(self):
        ver = m.scram_verifier('X'*64,b'0123456789abcdef')
        sql = m.install_sql({r: ver for r in m.ROLES})
        self.assertIn('ALTER ROLE bridge_chatgpt LOGIN PASSWORD',sql)
        self.assertIn('ALTER ROLE bridge_agent LOGIN PASSWORD',sql)
        self.assertEqual(sql.count('ALTER ROLE'),2)
        self.assertEqual(sql.count('COMMIT;'),1)
        self.assertNotIn('GRANT ',sql)
        self.assertNotIn('REVOKE ',sql)
        self.assertNotIn('DROP ',sql)
        self.assertNotIn('CREATE TABLE',sql)
        self.assertNotIn('INSERT ',sql)
        self.assertNotIn('UPDATE ',sql)
        self.assertNotIn('DELETE ',sql)

    def test_script_retains_authenticated_actor_guard(self):
        s = (HERE/'stage33b.py').read_text()
        self.assertIn('SELECT session_user, current_user',s)
        self.assertIn('CROSS_ACTOR_SET_ROLE_ALLOWED',s)
        self.assertIn('FORBIDDEN_FUNCTION_EXECUTED',s)
        self.assertIn('default_transaction_read_only = on',s)
        self.assertIn('SET ROLE ',s)

    def test_libpq_password_file_only(self):
        s = (HERE/'stage33b.py').read_text()
        self.assertIn("passfile=str(pgpass)",s)
        self.assertNotIn('password=passwds',s)
        self.assertIn('os.O_NOFOLLOW',s)
        self.assertIn('0o600',s)

    def test_preflight_considers_shadow_roles(self):
        self.assertIn('NOT rolcanlogin',m.PREFLIGHT)
        self.assertIn("'bridge_owner'",m.PREFLIGHT)
        self.assertIn("'bridge.messages'",m.PREFLIGHT)

    def test_no_legacy_or_worker_switch(self):
        for name in ('34_provision.sh','34_verify_only.sh'):
            s=(HERE/name).read_text()
            self.assertNotIn('systemctl',s)
            self.assertNotIn('opencode.json',s)
            self.assertNotIn('sed -i',s)
        self.assertNotIn('ALTER ROLE bridge_app', (HERE/'stage33b.py').read_text())

    def test_stage_id_and_no_special_secrets(self):
        self.assertEqual(m.ROLES,('bridge_chatgpt','bridge_agent'))
        self.assertEqual(m.denied_role('bridge_chatgpt'),'bridge_agent')
        self.assertEqual(m.denied_role('bridge_agent'),'bridge_chatgpt')
        for role in m.ROLES:
            self.assertEqual(m.config_path(role).suffix,'.pgpass')

if __name__ == '__main__':
    unittest.main()
