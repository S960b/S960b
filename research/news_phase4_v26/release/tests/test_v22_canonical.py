"""Stage-18 P0-A — prove there is exactly ONE implementation of v22_arms/ledger.

The defect: source/ carried an older copy of both modules, Python resolved them
ahead of source/v22_live/, and a fresh clone silently tested pre-stage-17 code.
The fix is a shim in v22_live that re-exports the canonical module. These tests
assert identity of the OBJECTS, not equality of text, so a re-introduced copy
fails here instead of at review time.
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent if (HERE.parent / "source").is_dir() else HERE
for _cand in (ROOT / "source", ROOT / "source" / "v22_live", ROOT):
    if (_cand / "v22_arms.py").is_file():
        sys.path.insert(0, str(_cand))
        break

import v22_arms as A
import v22_ledger as L


class CanonicalIdentityTests(unittest.TestCase):

    def test_arms_canonical_is_source_not_a_copy(self):
        path = Path(A.__file__).resolve()
        self.assertEqual(path.parent.name, "source",
                         f"v22_arms resolved to {path}, not source/v22_arms.py")

    def test_ledger_canonical_is_source_not_a_copy(self):
        path = Path(L.__file__).resolve()
        self.assertEqual(path.parent.name, "source",
                         f"v22_ledger resolved to {path}, not source/v22_ledger.py")

    def test_stage17_vocabulary_is_present_in_the_loaded_module(self):
        # This is the assertion the old shadow copy failed.
        for name in ("EV_CLIENT_NO_TOOLS", "EV_GATEWAY_NO_CALLS",
                     "EV_UPSTREAM_UNVERIFIED", "SEALABLE_TOOL_EVIDENCE"):
            self.assertTrue(hasattr(A, name), name)
        self.assertEqual(A.SEALABLE_TOOL_EVIDENCE, ("known_zero",))

    def test_mock_harness_does_not_claim_known_zero(self):
        rows = L.mock_arm_results("e" * 64,
                                  {"news": (0.5, 0, 0.5),
                                   "news_market": (0.5, 0, 0.5),
                                   "market": (0.5, 0, 0.5)})
        for row in rows:
            self.assertEqual(row["tool_evidence"], A.EV_UNKNOWN)
            self.assertIn("no_request_sent", row["tool_evidence_provenance"])

    def test_shim_reexports_the_same_objects_when_loaded_directly(self):
        import importlib.util
        shim_path = Path(A.__file__).resolve().parent / "v22_live" / "v22_arms.py"
        if not shim_path.is_file():
            self.skipTest("no shim in this layout")
        spec = importlib.util.spec_from_file_location("_shim_probe", shim_path)
        shim = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(shim)
        # Identity, not equality: the shim must hand back the canonical objects.
        self.assertIs(shim.build_prompts_3, A.build_prompts_3)
        self.assertEqual(Path(shim.CANONICAL_PATH).resolve(),
                         Path(A.__file__).resolve())
        self.assertIs(shim.validate_three_arms, A.validate_three_arms)

    def test_ledger_shim_reexports_the_same_objects(self):
        import importlib.util
        shim_path = Path(L.__file__).resolve().parent / "v22_live" / "v22_ledger.py"
        if not shim_path.is_file():
            self.skipTest("no shim in this layout")
        spec = importlib.util.spec_from_file_location("_shim_probe_l", shim_path)
        shim = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(shim)
        self.assertIs(shim.seal_triple, L.seal_triple)
        self.assertEqual(Path(shim.CANONICAL_PATH).resolve(),
                         Path(L.__file__).resolve())


if __name__ == "__main__":
    unittest.main()
