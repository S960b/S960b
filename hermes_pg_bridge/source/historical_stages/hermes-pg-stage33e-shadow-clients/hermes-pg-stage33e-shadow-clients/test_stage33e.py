"""Offline, no database/service mutations. Real DB tests in 37_verify_only.sh."""
import ast
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import actor_agent_cli as cli
import prepare_shadow_plugin as port
import verify33e as verify
import chatgpt_shadow_mcp as server

class Dummy:
    def health(self):return {'ok':True}
    def dialog_metadata(self,task_id):
        assert task_id==port.CANARY
        return {'state':'active','last_seq':4,'version':4,'title':'canary','next_actor':'chatgpt'}
    def delivery_status(self,task_id,seq):return {'status':'delivered'}
    def send_status(self,task_id,message_id):return {'found':True,'sender':'agent'}

class TestShadow(unittest.TestCase):
    def test_fixed_canary(self):
        self.assertEqual(cli.CANARY,port.CANARY)
        self.assertRaises(cli.CLIError,cli.validate_task,'task_'+'0'*32)
        cli.validate_task(port.CANARY)

    def test_writes_unavailable_in_shadow_cli(self):
        for op in ('append','ack','claim','create'):
            x=type('Args',(),{'command':op})()
            # Writes are rejected before importing bridge_pg or connecting.
            with patch.dict(sys.modules,{'bridge_pg.compat':type('Fake',(),{'version_token':None})()}):
                self.assertRaises(cli.CLIError,cli.dispatch,x,None)

    def test_plugin_entry_formats(self):
        self.assertEqual(verify.plugin_path('/tmp/a'),('/tmp/a',{}))
        self.assertEqual(verify.plugin_path(['/tmp/b',{'dry_run':False}]),('/tmp/b',{'dry_run':False}))
        self.assertRaises(verify.Stop,verify.plugin_path,['/tmp/c'])
        self.assertRaises(verify.Stop,verify.plugin_path,{'path':'/tmp/a'})

    def test_shadow_report_sanitized(self):
        r=server.status_from_actor(Dummy())
        self.assertTrue(r['pg_connected'])
        self.assertEqual(r['last_seq'],4)
        self.assertTrue(r['agent_report_present'])
        self.assertTrue(r['read_only'])
        self.assertNotIn('body',r)

    def test_bad_meta_fails(self):
        class Wrong(Dummy):
            def dialog_metadata(self,task_id):return {'last_seq':5,'state':'active'}
        self.assertRaises(ValueError,server.status_from_actor,Wrong())

    def test_plugin_port_exact_and_forced_dry_run(self):
        raw='''const dryRun = options.dry_run !== false;
const taskIds = Array.isArray(options.task_ids)
    ? options.task_ids.filter((x) => typeof x === "string" && /^task_[0-9a-f]{32}$/.test(x))
    : null;
const x = ["-m", "bridge_pg.cli", ...args];
const s = "-m bridge_pg.cli";
function latestPending(snapshot, seenSeq) {}
'''
        out=port.render(raw)
        self.assertIn('const dryRun = true;',out)
        self.assertIn('Object.freeze(["'+port.CANARY+'"]);',out)
        self.assertNotIn('bridge_pg.cli',out)
        self.assertEqual(out.count('actor_agent_cli.py'),2)

    def test_unexpected_plugin_layout_rejected(self):
        self.assertRaises(ValueError,port.render,'const dryRun = options.dry_run !== false;')

    def test_disallow_dryrun_bypass(self):
        src=Path('prepare_shadow_plugin.py').read_text()
        self.assertIn("'const dryRun = true; // STAGE33E FORCED READ ONLY'",src)
        self.assertNotIn('subprocess',src)

    def test_mcp_no_mutating_tools(self):
        src=Path('chatgpt_shadow_mcp.py').read_text()
        self.assertEqual(src.count('@mcp.tool('),2)
        self.assertNotIn('async def dialog_send(',src)
        self.assertNotIn('async def dialog_create(',src)
        self.assertNotIn('bridge_app',src)
        self.assertIn("HOST='127.0.0.1'",src)

    def test_static_cli_sql_select_only(self):
        src=Path('actor_agent_cli.py').read_text()
        self.assertIn('SELECT bridge.get_message',src)
        self.assertNotIn('bridge.claim_message(',src)
        self.assertNotIn('bridge.ack_outbox(',src)
        self.assertNotIn('bridge.send_message(',src)

    def test_no_live_config_mutations_in_installer(self):
        for f in ('37_preflight.sh','37_install_shadow_clients.sh','37_verify_only.sh'):
            s=Path(f).read_text()
            self.assertNotIn('opencode.json" >',s)
            self.assertNotIn('enable hermes-pg-v2-shadow-cg.service',s)
            self.assertNotIn('restart hermes-pg-tunnel-one-shot.service',s)

if __name__=='__main__':unittest.main()
