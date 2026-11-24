#!/usr/bin/env python3
"""Offline tests for the non-mutating revalidation package."""
import pathlib,unittest,subprocess,sys
D=pathlib.Path(__file__).parent
class TestRevalidation(unittest.TestCase):
 def test_sandbox_safely_uses_vm(self):
  s=(D/'test_candidate37b2c1.cjs').read_text();self.assertIn("require('node:vm')",s);self.assertIn('vm.createContext',s)
 def test_allowlist_field_in_context(self):
  s=(D/'test_candidate37b2c1.cjs').read_text();self.assertIn('taskIds:settings.taskIds',s)
 def test_negative_controls(self):
  s=(D/'test_candidate37b2c1.cjs').read_text()
  for f in ('missing_allowlist','empty_allowlist','alien_allowlist','preexisting_inflight','dry_run'):
   self.assertIn(f,s)
 def test_real_candidate_source(self):
  s=(D/'420_revalidate_candidate_only.sh').read_text();self.assertIn('render37b2c.py" verify',s)
 def test_original_not_rerendered(self):
  s=(D/'420_revalidate_candidate_only.sh').read_text();self.assertNotIn('render37b2c.py" render',s)
 def test_no_live_system_changes(self):
  s=(D/'420_revalidate_candidate_only.sh').read_text()
  for p in ('systemctl restart','managed_send_task','opencode.json >','managed_set_dispatch','managed-writes-stage34d-armed >'):
   self.assertNotIn(p,s)
 def test_python_check_is_no_mutation(self):
  s=(D/'check_old_harness37b2c1.py').read_text();self.assertNotIn('write_text(',s);self.assertNotIn('subprocess.',s)
 def test_real_filter_asserted(self):
  s=(D/'check_old_harness37b2c1.py').read_text();self.assertIn('ctx.taskIds',s);self.assertIn('EXPECTED_SRC',s)
 def test_only_new_private_root(self):
  s=(D/'420_revalidate_candidate_only.sh').read_text();self.assertIn('mkdir -m 700 -- "$INSTALL"',s)
 def test_node_explicit(self):
  s=(D/'420_revalidate_candidate_only.sh').read_text();self.assertIn('/home/kali/.hermes/node/bin/node',s)
 def test_original_is_pinned(self):
  s=(D/'check_old_harness37b2c1.py').read_text();self.assertIn('b3679086583d77c96b6ed1c3dc7b1ed1656707dabba4d3ac74843fe64177d730',s)
 def test_required_files(self):
  for f in ('test_candidate37b2c1.cjs','check_old_harness37b2c1.py','README.md','420_revalidate_candidate_only.sh'):self.assertTrue((D/f).is_file(),f)
if __name__=='__main__':unittest.main()
