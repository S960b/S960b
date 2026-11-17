"""Offline checks of ChatGPT-authored PAPER Tier-2 staged activation.

No test writes to the user's real HOME or makes model calls.
"""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "source", ROOT / "source" / "v22_live"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import tier2_activation as ACT
import tier2 as T2


class Tier2ActivationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "tier2"

    def test_status_inert_and_creates_no_files(self):
        got = ACT.status(root=self.data)
        self.assertEqual(got["status"], "unconfigured")
        self.assertFalse(self.data.exists())

    def test_stage_prepares_without_model_gate_or_trigger(self):
        got = ACT.stage(root=self.data)
        self.assertEqual(got["status"], "staged_no_model_gate")
        self.assertEqual(got["model_calls"], 0)
        p = ACT.paths(self.data)
        self.assertTrue(p["activation"].is_file())
        self.assertFalse(p["gate"].exists())
        self.assertFalse(p["trigger"].exists())
        rec = T2.read_activation(p["activation"])
        self.assertEqual(rec["max_model_invocations"], 3)
        self.assertEqual(rec["max_triples"], 1)
        self.assertEqual(rec["prompts_version"], T2.PROMPTS_VERSION)
        self.assertTrue(rec["prompt_only_no_web"])
        self.assertEqual(rec["upstream_tool_isolation"], "unverified")
        with self.assertRaises(T2.Tier2Error):
            T2.require_gate(gate=p["gate"], activation=p["activation"])

    def test_enable_gate_never_enables_trigger(self):
        ACT.stage(root=self.data)
        result = ACT.enable_gate(root=self.data)
        self.assertEqual(result["status"], "model_gate_enabled_trigger_disabled")
        p = ACT.paths(self.data)
        self.assertFalse(p["trigger"].exists())
        self.assertEqual(T2.require_gate(gate=p["gate"],
                                         activation=p["activation"])["trial_id"],
                         "tier2-paper-20261117-first")
        self.assertEqual(ACT.status(root=self.data)["status"], "gate_enabled")
        self.assertFalse(p["trial_db"].exists())

    def test_trigger_flag_blocks_gate(self):
        ACT.stage(root=self.data)
        p = ACT.paths(self.data)
        p["trigger"].touch()
        with self.assertRaisesRegex(ACT.ActivationError, "trigger_must_remain_disabled"):
            ACT.enable_gate(root=self.data)
        self.assertFalse(p["gate"].exists())

    def test_existing_trial_data_blocks_stage(self):
        self.data.mkdir()
        (self.data / "tier2.sqlite").write_bytes(b"existing")
        with self.assertRaisesRegex(ACT.ActivationError, "existing_tier2_state"):
            ACT.stage(root=self.data)
        self.assertFalse((self.data / "TIER2_ACTIVATION.json").exists())

    def test_existing_activation_never_overwritten(self):
        ACT.stage(root=self.data)
        path = ACT.paths(self.data)["activation"]
        before = path.read_bytes()
        with self.assertRaisesRegex(ACT.ActivationError, "existing_tier2_state"):
            ACT.stage(root=self.data)
        self.assertEqual(path.read_bytes(), before)

    def test_disarm_preserves_immutable_activation(self):
        ACT.stage(root=self.data)
        ACT.enable_gate(root=self.data)
        path = ACT.paths(self.data)["activation"]
        before = path.read_bytes()
        ACT.disarm(root=self.data)
        ACT.disarm(root=self.data)
        self.assertFalse(ACT.paths(self.data)["gate"].exists())
        self.assertEqual(path.read_bytes(), before)

    def test_14_day_window(self):
        now = datetime(2026, 11, 17, 15, tzinfo=timezone.utc)
        rec = ACT._record(trial_id="tier2-paper-20261117-first",
                          owner_seq=157, owner_task_id=ACT.OWNER_TASK_ID,
                          started=now - timedelta(days=13))
        self.assertTrue(ACT._check_age(rec, now=now))
        rec["started_utc"] = (now - timedelta(days=14)).isoformat()
        with self.assertRaisesRegex(ACT.ActivationError, "outside_14_day"):
            ACT._check_age(rec, now=now)

    def test_invalid_owner_reference_refused(self):
        with self.assertRaisesRegex(ACT.ActivationError, "owner_approval_reference"):
            ACT.stage(root=self.data, owner_seq=999)

    def test_prompt_is_explicitly_weaker_than_provider_proof(self):
        system = T2.ARM_SYSTEM_T2.lower()
        self.assertIn("do not browse", system)
        self.assertIn("call tools", system)
        self.assertIn("external", system)
        self.assertTrue(T2.PROMPTS_VERSION.endswith("prompt-no-web-v1"))


if __name__ == "__main__":
    unittest.main()
