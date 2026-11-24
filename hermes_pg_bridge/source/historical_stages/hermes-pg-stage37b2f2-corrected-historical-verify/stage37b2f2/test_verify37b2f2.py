import pathlib, importlib.util, sys, unittest
P=pathlib.Path(__file__).parent
sys.path.insert(0,str(P))
spec=importlib.util.spec_from_file_location('v37b2f2',P/'verify37b2f2.py');v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
class PairTests(unittest.TestCase):
 def test_true_pair(self):self.assertTrue(v.corrected_pair_match([[99,101]],99,'101',lambda _:101))
 def test_list_not_hashable_regression(self):self.assertTrue(v.corrected_pair_match([[99,101]],99,'101',lambda _:101))
 def test_unrelated_pid(self):self.assertFalse(v.corrected_pair_match([[99,101]],100,'101',lambda _:101))
 def test_reused_pid_different_ticks(self):self.assertFalse(v.corrected_pair_match([[99,102]],99,'101',lambda _:101))
 def test_proc_ticks_mismatch(self):self.assertFalse(v.corrected_pair_match([[99,101]],99,'101',lambda _:102))
 def test_process_exited(self):self.assertFalse(v.corrected_pair_match([[99,101]],99,'101',lambda _:(_ for _ in()).throw(FileNotFoundError())))
 def test_bad_pid_type(self):self.assertFalse(v.corrected_pair_match([[99,101]],True,'101',lambda _:101))
 def test_bad_ticks(self):self.assertFalse(v.corrected_pair_match([[99,101]],99,'0',lambda _:101))
 def test_bad_pair_shape(self):self.assertFalse(v.corrected_pair_match([['99',101],[],[99,'101']],99,'101',lambda _:101))
 def test_pairs_not_list(self):self.assertFalse(v.corrected_pair_match({'99':101},99,'101',lambda _:101))
class DiagnosticTests(unittest.TestCase):
 def test_preserve_foreign_stop(self):self.assertEqual(v.diagnostic_from_foreign_stop(RuntimeError('STAGE37B2D_STOP_MANAGED_LOCK_PRESENT')),'STAGE37B2D_STOP_MANAGED_LOCK_PRESENT')
 def test_preserve_local_stop(self):self.assertEqual(v.diagnostic_from_foreign_stop(RuntimeError('STAGE37B2F_STOP_OTHER')),'STAGE37B2F_STOP_OTHER')
 def test_no_secrets(self):self.assertEqual(v.diagnostic_from_foreign_stop(RuntimeError('secret=abc')),'STAGE37B2F2_STOP_RuntimeError')
 def test_other_prefix(self):self.assertEqual(v.diagnostic_from_foreign_stop(RuntimeError('STAGE37B2A_STOP_TEST')),'STAGE37B2F2_STOP_RuntimeError')
 def test_source_anchors_positive(self):
  s=("if str(pid) in set(mt) and proc_start(pid)==ticks\n"
     "except (FileNotFoundError,OSError,ValueError,TypeError,KeyError):continue\n"
     "str(exc) if isinstance(exc,Stop) else 'STAGE37B2F_STOP_'+type(exc).__name__")
  self.assertTrue(v.original_bug_pins(s))
 def test_source_anchors_negative(self):self.assertFalse(v.original_bug_pins('def verify(): pass'))
if __name__=='__main__':unittest.main()
