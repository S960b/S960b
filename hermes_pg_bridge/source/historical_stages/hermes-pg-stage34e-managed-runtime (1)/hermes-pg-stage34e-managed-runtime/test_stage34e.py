"""Offline tests. These tests NEVER contact PostgreSQL, MCP, Git or SDK."""
from __future__ import annotations
import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path
import render34e as r

PARTS=r.parts(r.PARTS.read_text('utf-8')) if r.PARTS.exists() else r.parts(Path(__file__).with_name('parts34e.js').read_text('utf-8'))

# A minimal fixture with the same transform anchors and guards as pinned Stage34D.
DUMMY=f'''const STATE_DIR = join(HOME, ".local/state/mt-pg-inbox-managed-stage34d");
const dryRun = true; // STAGE34D SHADOW, SDK PROHIBITED UNTIL SOURCE REWRITE
const taskIds = null;
function cli(request, timeoutMs = CLI_TIMEOUT_MS) {{
  return Promise.resolve(request);
}}
function buildPrompt(dialog, message) {{
  return "PGV2_ROLE_BOUND_MT_SDK_ACK_20261122";
}}
function latestPending(snapshot, seenSeq) {{
  return null;
}}
async function dispatch(ctx, dialog, message, state) {{
  const key = dialog.task_id;
  const lease = {{delivery_id:1,lease_token:'aaaa'}};
  state.inflight[key] = {{seq:message.seq}};
  await ctx.client.session.prompt({{}});
  const acked = await ctx.cliCall({{op:"ack",delivery_id:lease.delivery_id,
                                   lease_token:lease.lease_token}});
  return acked;
}}
function confirmedMtResponseAfter(snapshot, taskId, reserved) {{
  return true;
}}
async function reconcileAnsweredInflight(ctx, state, dialog) {{
  state.seen[dialog.task_id] = 1;
  delete state.inflight[dialog.task_id];
  ctx.log("inflight_reconciled_ack_confirmed");
  return true;
}}
export async function runTick(ctx) {{
  if (ctx.taskIds && !ctx.taskIds.includes(dialog.task_id)) return false;
  return true;
}}
export function diagnosticContext(v) {{ return v; }}
'''

class PatchTests(unittest.TestCase):
    def test_parts_all_sections_present(self):
        self.assertEqual(set(PARTS),{'CLI','PROMPT','CONFIRMED','RECONCILE','TICK'})

    def test_render_exact_known_shape(self):
        out=r.render(DUMMY,PARTS)
        self.assertIn('const dryRun = true;',out)
        self.assertIn('mt-pg-inbox-managed-stage34e',out)
        self.assertIn('for (const taskId of Object.keys(state.inflight).sort())',out)

    def test_old_canary_instruction_removed(self):
        out=r.render(DUMMY,PARTS)
        self.assertNotIn('PGV2_ROLE_BOUND_MT_SDK_ACK_20261122',out)

    def test_unique_function_anchors(self):
        out=r.render(DUMMY,PARTS)
        for x in ('function cli(', 'function buildPrompt(',
                  'function confirmedMtResponseAfter(',
                  'async function reconcileAnsweredInflight(',
                  'export async function runTick('):
            self.assertEqual(out.count(x),1,x)

    def test_claim_consumer_is_managed(self):
        out=r.render(DUMMY,PARTS)
        self.assertIn('"--consumer", "mt-pg-managed"',out)
        self.assertNotIn('"--consumer", "mt-pg-inbox"',out)

    def test_ack_arguments_bound_to_task_and_seq(self):
        out=r.render(DUMMY,PARTS)
        self.assertIn('op:"ack", task_id:key, seq:message.seq',out)
        self.assertIn('"--task-id", taskId, "--seq"',out)

    def test_dispatch_requires_durable_exact_report_before_ack(self):
        out=r.render(DUMMY,PARTS)
        sec=out.split('async function dispatch(',1)[1].split('function confirmedMtResponseAfter(',1)[0]
        self.assertIn('managed_report_not_confirmed',sec)
        self.assertLess(sec.index('managed_report_not_confirmed'),sec.index('op:"ack", task_id:key'))

    def test_lost_ack_holds_inflight(self):
        out=r.render(DUMMY,PARTS)
        sec=out.split('async function reconcileAnsweredInflight',1)[1].split('export async function runTick',1)[0]
        self.assertIn('if (ack?.acked !== true) return false;',sec)
        self.assertLess(sec.index('if (ack?.acked !== true) return false;'),sec.index('delete state.inflight[taskId]'))

    def test_inflight_before_dispatch_list(self):
        tick=PARTS['TICK']
        self.assertLess(tick.index('Object.keys(state.inflight)'),tick.index('await listAll(ctx.cliCall)'))

    def test_no_automatic_inflight_clear_on_missing_dialog(self):
        out=r.render(DUMMY,PARTS)
        self.assertNotIn('inflight_cleared_deleted',out)

    def test_no_git_transport(self):
        out=r.render(DUMMY,PARTS)
        for x in ('bridge_pg.cli','direct_git','GitHub API','mt-git-inbox'):
            self.assertNotIn(x,out)

    def test_reject_source_without_expected_ack_call(self):
        with self.assertRaisesRegex(r.Refused,'dispatch_ack_shape_changed'):
            r.render(DUMMY.replace('lease_token:lease.lease_token','lease_token:other.token'),PARTS)

    def test_reject_source_with_extra_ack_call(self):
        bad=DUMMY.replace('return acked;','const x=await ctx.cliCall({op:"ack", delivery_id:lease.delivery_id, lease_token:lease.lease_token});\n  return acked;')
        with self.assertRaises(r.Refused):r.render(bad,PARTS)

    def test_reject_changed_function_boundaries(self):
        bad=DUMMY.replace('export function diagnosticContext(v)','function diagnosticContext(v)')
        with self.assertRaises(r.Refused):r.render(bad,PARTS)

    def test_reject_changed_dryrun(self):
        bad=DUMMY.replace(r.OLD_DRY,'const dryRun = false;')
        with self.assertRaises(r.Refused):r.render(bad,PARTS)

    def test_reject_changed_state(self):
        bad=DUMMY.replace(r.OLD_STATE,'const STATE_DIR = "UNKNOWN";')
        with self.assertRaises(r.Refused):r.render(bad,PARTS)

    def test_reject_duplicate_anchors(self):
        bad=DUMMY.replace('function buildPrompt(dialog, message) {',
            'function buildPrompt(dialog, message) {\n  // another function buildPrompt(dialog, message) {')
        with self.assertRaises(r.Refused):r.render(bad,PARTS)

    def test_old_plugin_state_not_shared(self):
        code=r.render(DUMMY,PARTS)
        self.assertNotIn('mt-pg-inbox-managed-stage34d',code)
        self.assertNotIn('mt-pg-inbox-agent-stage33f',code)

    def test_arm_gate_is_not_shipped(self):
        self.assertNotIn('managed-writes-stage34d-armed',[p.name for p in Path(__file__).parent.iterdir()])

    def test_no_sdk_in_install_scripts(self):
        for script in Path(__file__).parent.glob('*.sh'):
            src=script.read_text()
            for token in ('systemctl --user restart multitool','systemctl --user stop multitool',
                          'ALTER ROLE','CREATE FUNCTION','claim --','ack --','append --',
                          'cp -- "$DRAFT" "$CFG"'):
                self.assertNotIn(token,src,(script.name,token))

    def test_managed_cli_remains_byte_pinned(self):
        self.assertEqual(r.CLI_SHA,'bf8cad69a02e71a70ec3377504c24a3fc2cb71a38f2c8c4a025abc587b774485')

    def test_pins_include_existing_candidate_sha(self):
        self.assertEqual(r.SOURCE_SHA,'ea06a7fb0fc7c3aa26abb5678de380776e634706a6146fe5b9340a33be956c2d')

    def test_no_direct_managed_sql_in_js(self):
        txt='\n'.join(PARTS.values())
        for x in ('SELECT bridge.messages','SELECT bridge.outbox','SET ROLE','bridge_app'):
            self.assertNotIn(x,txt)

    def test_dryrun_not_defeated_by_options(self):
        self.assertIn(r.NEW_DRY,r.render(DUMMY,PARTS))
        self.assertNotIn('options.dry_run !== false',r.render(DUMMY,PARTS))

    def test_exact_task_guard_in_source(self):
        self.assertIn('const taskIds = null;',r.render(DUMMY,PARTS))
        self.assertIn('ag_dispatch_list',r.render(DUMMY,PARTS))

if __name__=='__main__':unittest.main()
