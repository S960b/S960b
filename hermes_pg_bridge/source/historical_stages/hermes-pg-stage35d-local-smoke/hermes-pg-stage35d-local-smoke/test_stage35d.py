"""Offline regression tests: never inspect real credentials, no SSH, no SDK."""
import io,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
import stage35d as d
import smoke35d as s
class PrefTests(unittest.TestCase):
    def test_01_config_sha(self):self.assertEqual(len(d.CFG_SHA),64)
    def test_02_tunnel_launcher_sha(self):self.assertEqual(len(d.OLD_SHA),64)
    def test_03_35c_source_hashes(self):self.assertEqual(len(d.S35C_SOURCES),3)
    def test_04_pinned_names(self):self.assertIn('managed_mcp35c.py',d.S35C_SOURCES)
    def test_05_pin_one_secret(self):self.assertEqual(d.SECRET.name,'managed-mcp-local-key35c')
    def test_06_missing_permit(self):self.assertEqual(d.PERMIT.name,'managed-mcp-write-permit35c.json')
    def test_07_tunnel_target_pinned(self):self.assertIn('stage33fd',str(d.OLD))
    def test_08_local_only_url(self):self.assertEqual(s.URL,'http://127.0.0.1:18777/mcp')
    def test_09_need_error(self):
        with self.assertRaisesRegex(d.Stop,'test_code'):d.need(False,'test_code')
    def test_10_need_success(self):d.need(True,'noop')
    def test_11_bin_support_positive(self):
        fake=MagicMock(returncode=0,stdout='Usage: tunnel-client run\n'+('x'*200)+'\n --mcp.extra-headers',stderr='')
        with patch('pathlib.Path.is_file',return_value=True),patch.object(d.os,'access',return_value=True),patch.object(d.subprocess,'run',return_value=fake):d.client_capability()
    def test_12_bin_support_missing(self):
        fake=MagicMock(returncode=0,stdout='Usage: tunnel-client run\n'+('x'*200),stderr='')
        with patch('pathlib.Path.is_file',return_value=True),patch.object(d.os,'access',return_value=True),patch.object(d.subprocess,'run',return_value=fake):
            with self.assertRaisesRegex(d.Stop,'LACKS_MCP_EXTRA_HEADERS'):d.client_capability()
    def test_13_cli_not_executable(self):
        with patch('pathlib.Path.is_file',return_value=False):
            with self.assertRaisesRegex(d.Stop,'UNAVAILABLE'):d.client_capability()
    def test_14_cli_help_error(self):
        fake=MagicMock(returncode=2,stdout='usage '*80,stderr='')
        with patch('pathlib.Path.is_file',return_value=True),patch.object(d.os,'access',return_value=True),patch.object(d.subprocess,'run',return_value=fake):
            with self.assertRaisesRegex(d.Stop,'HELP_FAILED'):d.client_capability()
    def test_15_cli_help_timeout(self):
        import subprocess
        with patch('pathlib.Path.is_file',return_value=True),patch.object(d.os,'access',return_value=True),patch.object(d.subprocess,'run',side_effect=subprocess.TimeoutExpired('client',15)):
            with self.assertRaisesRegex(d.Stop,'HELP_UNAVAILABLE'):d.client_capability()
    def test_16_client_no_secret_env(self):
        fake=MagicMock(returncode=0,stdout='--mcp.extra-headers '+'x'*200,stderr='')
        with patch('pathlib.Path.is_file',return_value=True),patch.object(d.os,'access',return_value=True),patch.object(d.subprocess,'run',return_value=fake) as run:
            d.client_capability();env=run.call_args.kwargs['env']
            self.assertNotIn('CONTROL_PLANE_API_KEY',env)
            self.assertNotIn('MCP_EXTRA_HEADERS',env)
    def test_17_no_service_start_in_py(self):
        source=Path(d.__file__).read_text()
        self.assertNotIn('systemctl --user start',source)
    def test_18_config_unchanged_in_source(self):
        source=Path(d.__file__).read_text()
        self.assertNotIn('write_bytes(',source)
        self.assertNotIn('os.unlink(',source)
    def test_19_no_legacy_bridge_tool(self):self.assertNotIn('git-dialog',Path(d.__file__).read_text())
    def test_20_main_bad_command(self):self.assertEqual(d.main(['bad']),2)
class SmokeTests(unittest.TestCase):
    def test_21_parse_json(self):self.assertEqual(s.parse_mcp(b'{"result":{"tools":[]}}')['result']['tools'],[])
    def test_22_parse_sse(self):
        body=b'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'
        self.assertTrue(s.parse_mcp(body)['result']['ok'])
    def test_23_parse_bad_frame(self):
        with self.assertRaises(s.SmokeError):s.parse_mcp(b'no response')
    def test_24_parse_bad_json(self):
        with self.assertRaises((ValueError,s.SmokeError)):s.parse_mcp(b'{bad')
    def test_25_read_only_rpc_calls(self):
        src=Path(s.__file__).read_text()
        self.assertIn("'managed_health'",src)
        self.assertIn("'managed_list'",src)
        for name in ('managed_create','managed_set_dispatch','managed_send_task','managed_ack_report'):
            self.assertNotIn("'name':'"+name+"'",src)
    def test_26_mcp_auth_key_local_only(self):self.assertIn('static_guard35c import local_secret',Path(s.__file__).read_text())
    def test_27_unknown_not_public(self):self.assertNotIn('print(secret)',Path(s.__file__).read_text())
    def test_28_protocol_version(self):self.assertIn('2025-06-18',Path(s.__file__).read_text())
    def test_29_local_ping(self):self.assertIn('/ping',Path(s.__file__).read_text())
    def test_30_payload_size_bounded(self):self.assertIn('r.read(131072)',Path(s.__file__).read_text())
    def test_31_no_shell_in_smoke(self):self.assertNotIn('subprocess',Path(s.__file__).read_text())
    def test_32_no_tunnel_changes_in_scripts(self):
        root=Path(__file__).parent
        scripts='\n'.join(x.read_text() for x in root.glob('*.sh'))
        for forbidden in ('systemctl --user restart hermes-pg-tunnel', 'systemctl --user stop hermes-pg-tunnel', 'MCP_SERVER_URL='):
            self.assertNotIn(forbidden,scripts)
    def test_33_stage35c_stop_only(self):
        src=(Path(__file__).parent/'123_stop_local_shadow.sh').read_text()
        self.assertIn('stop hermes-pg-stage35c-static-shadow.service',src)
        self.assertNotIn('stop hermes-pg-tunnel',src)
    def test_34_attempt_marker(self):self.assertIn('local-shadow-start-attempt.json',(Path(__file__).parent/'121_start_local_shadow_once.sh').read_text())
    def test_35_old_tunnel_available(self):self.assertEqual(len(d.RUN_UNITS),3)
    def test_36_no_write_gate(self):self.assertIn('MANAGED_WRITE_PERMIT_PRESENT',Path(d.__file__).read_text())
    def test_37_explicit_fail_closed_unsupported(self):self.assertIn('LACKS_MCP_EXTRA_HEADERS_FLAG',Path(d.__file__).read_text())
    def test_38_not_enable_systemd(self):
        scripts='\n'.join(x.read_text() for x in Path(__file__).parent.glob('*.sh'))
        self.assertNotIn('systemctl --user enable',scripts)
    def test_39_no_secret_echo(self):
        scripts='\n'.join(x.read_text() for x in Path(__file__).parent.glob('*.sh'))
        self.assertNotIn('cat /home/kali/.config/bridge-pg-v2/managed-mcp-local-key35c',scripts)
    def test_40_no_file_creation_for_write_permit(self):
        files='\n'.join(x.read_text() for x in Path(__file__).parent.glob('*.sh'))
        self.assertNotIn('managed-mcp-write-permit35c.json',files)
    def test_41_tool_payload_structured(self):
        self.assertTrue(s.tool_payload({'structuredContent':{'ok':True,'result':{'connected':True}}})['ok'])
    def test_42_tool_payload_text_content(self):
        val=s.tool_payload({'content':[{'type':'text','text':'{"ok":true,"result":[]}'}]})
        self.assertEqual(val['result'],[])
    def test_43_tool_payload_failure(self):
        with self.assertRaises(s.SmokeError):s.tool_payload({'isError':True,'content':[]})
    def test_44_tool_payload_missing(self):
        with self.assertRaises(s.SmokeError):s.tool_payload({'content':[]})
    def test_45_local_registry_pilot_pin(self):
        self.assertIn('task_bddaadef89b7458fb7f8cf437dc0f023',Path(s.__file__).read_text())
if __name__=='__main__':unittest.main()
