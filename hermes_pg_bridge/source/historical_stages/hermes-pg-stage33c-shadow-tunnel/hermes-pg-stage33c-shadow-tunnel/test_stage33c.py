import os, pathlib, re, unittest
from unittest.mock import patch

import actor_adapter as a
import ssh_shadow_runner as t
ROOT = pathlib.Path(__file__).resolve().parent

class Stage33C(unittest.TestCase):
    def test_shadow_forward_strict_host_key_and_no_shell(self):
        src=(ROOT/'ssh_shadow_runner.py').read_text()
        for token in ('StrictHostKeyChecking=yes','ExitOnForwardFailure=yes','IdentitiesOnly=yes',
                      'ServerAliveInterval=20','ServerAliveCountMax=3','127.0.0.1:15433:127.0.0.1:5432',
                      "os.execv('/usr/bin/ssh', argv)"):
            self.assertIn(token,src)
        self.assertNotIn('shell=True',src)
        self.assertNotIn('StrictHostKeyChecking=no',src)
    def test_target_validation(self):
        for target in ('root@REDACTED_IPv4', 'bridge-host.example', 'bridge_user@host'):
            self.assertEqual(t.validate_target(target),target)
        for target in ('', '-p 2222 user@host', 'foo;rm -rf /', 'root@host\nssh-other', '../bad', 'user@@host', 'a'*260):
            with self.assertRaises(t.Stop):t.validate_target(target)
    def test_adapter_separate_exposed_methods(self):
        chat=a.ChatGPTActorDB()
        worker=a.AgentActorDB()
        self.assertEqual(chat._role,'bridge_chatgpt')
        self.assertEqual(worker._role,'bridge_agent')
        for only_chat in ('create_dialog','send_message','return_turn','tombstone','creation_status'):
            self.assertTrue(callable(getattr(chat,only_chat)))
            self.assertFalse(hasattr(worker,only_chat),only_chat)
        for only_worker in ('send_report','claim_message','claim_outbox','ack_outbox','release_outbox'):
            self.assertTrue(callable(getattr(worker,only_worker)))
            self.assertFalse(hasattr(chat,only_worker),only_worker)
    def test_adapter_fixed_actors_and_sql_wrappers(self):
        with patch.object(a._Base,'_one',return_value={'created':True}) as call:
            a.ChatGPTActorDB().send_message('task_x','mid','task','body')
            self.assertIn('bridge.cg_send_message(',call.call_args.args[0])
            self.assertNotIn('bridge.send_message(',call.call_args.args[0])
            self.assertEqual(call.call_args.args[1],('task_x','mid','task','body',None))
        with patch.object(a._Base,'_one',return_value={'created':True}) as call:
            a.AgentActorDB().send_report('task_x','mid','report','body')
            self.assertIn('bridge.ag_send_message(',call.call_args.args[0])
            self.assertEqual(call.call_args.args[1],('task_x','mid','report','body',None))
    def test_actor_password_never_from_environment(self):
        src=(ROOT/'actor_adapter.py').read_text()
        self.assertIn('os.O_NOFOLLOW',src)
        self.assertIn('stat.S_IMODE(st.st_mode) != 0o600',src)
        self.assertIn("'PGPASSWORD','PGSERVICE','PGOPTIONS'",src)
        self.assertIn('session_user AS session, current_user AS current',src)
        self.assertNotIn('subprocess',src)
        self.assertNotIn('SELECT bridge.send_message(',src)
    def test_package_does_not_modify_old_unit_or_db(self):
        text=(ROOT/'35_install_shadow_tunnel.sh').read_text()
        self.assertIn('15433',text)
        self.assertIn('STAGE33C_LEGACY_15432_AND_ALL_OTHER_SERVICES_UNCHANGED',text)
        self.assertNotIn('systemctl --user stop bridge-pg-v2-mcp',text)
        for disallowed in ('ALTER ROLE','GRANT ','REVOKE ','DROP DATABASE','DELETE FROM bridge.'):
            self.assertNotIn(disallowed,text)
    def test_credential_role_mismatch_rejected(self):
        with self.assertRaises(a.AdapterError):a._password_for('bridge_owner')
        with self.assertRaises(a.AdapterError):a._Base('bridge_app')
    def test_runner_has_separate_service_and_optional_removal(self):
        src=(ROOT/'35_install_shadow_tunnel.sh').read_text()
        self.assertIn('NoNewPrivileges=true',src)
        self.assertIn('Restart=always',src)
        self.assertIn('bridge-pg-v2-ssh-shadow.service',src)
        self.assertIn('trap install_rollback EXIT',src)
        remove=(ROOT/'35_disable_shadow_tunnel.sh').read_text()
        self.assertNotIn('15432',remove.split('echo')[0])

if __name__=='__main__':unittest.main()
