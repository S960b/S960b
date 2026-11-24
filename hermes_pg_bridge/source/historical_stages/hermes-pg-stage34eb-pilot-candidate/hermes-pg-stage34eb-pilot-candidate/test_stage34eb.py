import unittest
from pathlib import Path
import pilot34eb as p

GATE=Path(__file__).with_name('pilot_gate34eb.js')
def fixture():
    return (
      'const STATE_DIR = join(HOME, ".local/state/mt-pg-inbox-managed-stage34e");\n'
      + p.OLD_DRY + '\n' + p.OLD_TASKIDS + '\n' + 'const ctx={taskIds: taskIds && taskIds.length ? taskIds : null};\n'
      + """function buildPrompt(){}
function managedReportId(){}
function confirmedMtResponseAfter(){}
async function reconcileAnsweredInflight(){}
export async function runTick(ctx) {
  for (const taskId of Object.keys(state.inflight).sort()) {}
  const dialogs = await listAll(ctx.cliCall);
    for (const dialog of dialogs) {
  }
}
async function dispatch() {
  const x=await ctx.cliCall({op:"ack", task_id:key, seq:message.seq});
  if (!confirmedMtResponseAfter()) throw new Error("managed_report_not_confirmed");
}
const marker1="managed_agent_cli34d.py";
const marker2="ag_dispatch_list";
const marker3="managed_inflight_ack_confirmed";
const marker4="session.prompt";
""")
class TestStage34EB(unittest.TestCase):
    def test_source_pin(self):
        self.assertEqual(len(p.SOURCE_SHA),64)
        self.assertEqual(p.SOURCE_SHA,'1580178d508174af1edff784b87acd11d339b1d306a057f6112786ffb36122e8')
    def test_config_pin(self):
        self.assertEqual(p.CONFIG_SHA,'e5320f6e971073d5d2c43a64ce85d165cb49dc9a94e6cf7806009ebc2b59ded1')
    def test_cli_pin(self):
        self.assertEqual(len(p.CLI_SHA),64)
    def test_gate_source_exact(self):
        self.assertEqual(GATE.read_text(),p.PILOT_FN)
    def test_gate_rejects_canary_static(self):
        self.assertIn(p.CANARY,p.PILOT_FN)
    def test_gate_rejects_multiple(self):
        self.assertIn('ids.length !== 1',p.PILOT_FN)
    def test_gate_requires_task_format(self):
        self.assertIn('/^task_[0-9a-f]{32}$/.test',p.PILOT_FN)
    def test_gate_returns_immutable_array(self):
        self.assertIn('Object.freeze([ids[0]])',p.PILOT_FN)
    def test_filter_is_fail_closed(self):
        self.assertTrue(p.NEW_FILTER.startswith('if (!ctx.taskIds ||'))
        self.assertNotIn('&& !ctx.taskIds.includes',p.NEW_FILTER)
    def test_pin_no_gate(self):
        self.assertEqual(p.ARM.name,'managed-writes-stage34d-armed')
    def test_input_requires_exact_anchors(self):
        with self.assertRaises(p.Refused):p.render('hello')
    def test_input_rejects_duplicate_dryrun(self):
        with self.assertRaises(p.Refused):p.render(fixture()+p.OLD_DRY)
    def test_input_rejects_legacy_broad_cli(self):
        with self.assertRaises(p.Refused):p.render(fixture()+'bridge_pg.cli')
    def test_input_rejects_legacy_git(self):
        with self.assertRaises(p.Refused):p.render(fixture()+'mt-git-inbox.mjs')
    def test_reconcile_before_discovery(self):
        q=fixture().replace('  const dialogs = await listAll(ctx.cliCall);','')
        with self.assertRaises(p.Refused):p.render(q)
    def test_no_sdk_or_db_in_prepare(self):
        import inspect
        src=inspect.getsource(p.prepare)
        self.assertNotIn('subprocess',src)
        self.assertNotIn('psycopg',src)
        self.assertNotIn('systemctl',src)
    def test_no_change_to_existing_config(self):
        import inspect
        src=inspect.getsource(p.prepare)
        self.assertNotIn("open(CONFIG",src)
        self.assertNotIn("CONFIG.write",src)
    def test_unregistered_output_is_separate(self):
        self.assertNotEqual(p.SRC,p.DST)
        self.assertNotEqual(p.ACTIVE,p.DST)
        self.assertEqual(p.DST.name,'mt-pg-inbox-managed-stage34eb-pilot-unregistered.mjs')
    def test_no_run_on_stage34e_shadow(self):
        self.assertIn('const dryRun = true;',p.OLD_DRY)
        self.assertIn('const dryRun = false;',p.NEW_DRY)
    def test_no_task_without_registered_id(self):
        self.assertIn('return Object.freeze([])',p.PILOT_FN)
    def test_canary_filter_preserved(self):
        import inspect
        self.assertIn('entry[1].get',inspect.getsource(p.pin))
        self.assertIn('CANARY',inspect.getsource(p.pin))
    def test_no_state_replacement(self):
        self.assertNotIn('state.json',p.NEW_TASKIDS)
    def test_cli_no_sql_privilege_change(self):
        self.assertIn('managed_agent_cli34d.py',p.render(fixture()))
    def test_dryrun_old_source_fails_to_patch_again(self):
        patched=p.render(fixture())
        with self.assertRaises(p.Refused):p.render(patched)
    def test_render_pilot_guard_in_tick(self):
        source=p.render(fixture())
        self.assertEqual(source.count(p.NEW_FILTER),1)
        self.assertNotIn(p.OLD_DRY,source)
        self.assertNotIn(p.OLD_TASKIDS,source)
        self.assertTrue(source.index('for (const taskId of Object.keys(state.inflight).sort())') <
                        source.index('for (const dialog of dialogs) {'))
    def test_rendered_fixture_node_syntax(self):
        import tempfile,subprocess
        source=p.render(fixture())
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'probe.mjs';file.write_text(source)
            import shutil
            node=shutil.which('node')
            if not node:self.skipTest('node_missing')
            r=subprocess.run([node,'--check',str(file)],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
if __name__=='__main__':unittest.main()
