"""Offline guards. Does not connect to DB, MCP, SSH or SDK."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import verify33fd as v

class Checks(unittest.TestCase):
    def setUp(self):
        self.base={'plugin':[
            ['/home/kali/x'+str(i)+'.mjs',{}] for i in range(6)
        ]+[[str(v.PLUGIN),{'dry_run':False,'task_ids':[v.CANARY],'tick_ms':30000}]]}
    def valid(self):
        x=json.dumps(self.base).encode()
        with patch.object(v,'digest',return_value=v.CONFIG_SHA):
            return v.validated_config(x)
    def test_exact_seven_plugins(self):self.assertTrue(self.valid())
    def test_reject_canary_filter_removed(self):
        self.base['plugin'][-1][1]['task_ids']=[]
        with patch.object(v,'digest',return_value=v.CONFIG_SHA):
            with self.assertRaisesRegex(v.Stop,'PG_PLUGIN_OPTIONS_DRIFT'): v.validated_config(json.dumps(self.base).encode())
    def test_reject_disabled_role_plugin(self):
        self.base['plugin'][-1][1]['dry_run']=True
        with patch.object(v,'digest',return_value=v.CONFIG_SHA):
            with self.assertRaises(v.Stop):v.validated_config(json.dumps(self.base).encode())
    def test_reject_multiple_pg_consumers(self):
        self.base['plugin'][0]=['/tmp/mt-pg-inbox-canary.mjs',{'dry_run':False}]
        with patch.object(v,'digest',return_value=v.CONFIG_SHA):
            with self.assertRaises(v.Stop):v.validated_config(json.dumps(self.base).encode())
    def test_reject_git_plugin_surviving(self):
        self.base['plugin'][0]=['/tmp/mt-git-inbox.mjs',{}]
        with patch.object(v,'digest',return_value=v.CONFIG_SHA):
            with self.assertRaisesRegex(v.Stop,'LEGACY_PLUGIN_PRESENT'):v.validated_config(json.dumps(self.base).encode())
    def test_reject_wrong_count(self):
        self.base['plugin'].append('/tmp/extra')
        with patch.object(v,'digest',return_value=v.CONFIG_SHA):
            with self.assertRaises(v.Stop):v.validated_config(json.dumps(self.base).encode())
    def test_reject_config_hash_drift(self):
        with self.assertRaisesRegex(v.Stop,'LIVE_CONFIG_DRIFT'): v.validated_config(json.dumps(self.base).encode())
    def test_controlled_tunnel_target(self):
        t=Path('run_stage33fd_tunnel.sh').read_text()
        self.assertIn("MCP_SERVER_URL='http://127.0.0.1:18773/mcp'",t)
        self.assertIn("CONTROL_PLANE_TUNNEL_ID='tunnel_6b02fe0b07f4819194b5adf2ff5e6a6a'",t)
        self.assertIn("MCP_OAUTH_DISCOVERY='disabled'",t)
        self.assertNotIn('18769/mcp',t)
        self.assertNotIn('git',t.lower())
    def test_no_auto_arm_in_installer(self):
        t=Path('41_install_and_switch.sh').read_text()
        self.assertNotIn('ALLOW_FIXED_SEQ5_AFTER_EXCLUSIVE_SDK_SWITCH',t)
        self.assertNotIn('stage33f_send_once',t)
        self.assertNotIn('session.prompt',t)
    def test_arming_is_separate_and_requires_confirmation(self):
        t=Path('42_arm_after_plugin_check.sh').read_text()
        self.assertIn('STAGE33FD_CHATGPT_PLUGIN_VERIFIED',t)
        self.assertIn('YES_ROLE_BOUND_18773',t)
        self.assertIn('verify33fd.py switched',t)
        self.assertIn('noclobber',t)
    def test_rollback_cannot_work_after_arming(self):
        t=Path('43_rollback_before_arm.sh').read_text()
        self.assertIn('STAGE33FD_STOP_ROLLBACK_BLOCKED_ARMED',t)
        self.assertIn('! -e "$ARM"',t)
    def test_installer_restores_old_tunnel_on_failure(self):
        t=Path('41_install_and_switch.sh').read_text()
        self.assertIn('STAGE33FD_ROLLBACK_OLD_TUNNEL_RESTART_REQUESTED',t)
        self.assertIn('systemctl --user start "$OLD"',t)
        self.assertIn('STAGE33FD_NEW_TUNNEL_ROLLED_BACK_NO_DB_WRITE',t)
    def test_read_only_status_has_no_mutating_calls(self):
        t=Path('44_readonly_status.sh').read_text()
        for forbidden in ('stage33f_send_once','ag_claim_message','send_message(', 'ack_outbox(', 'session.prompt('):
            self.assertNotIn(forbidden,t)
    def test_marker_logic_source_pinned(self):
        self.assertEqual(len(v.LOGIC_SHA),64)
        self.assertEqual(len(v.SERVER_SHA),64)
        self.assertEqual(len(v.CONFIG_SHA),64)

if __name__=='__main__':unittest.main()
