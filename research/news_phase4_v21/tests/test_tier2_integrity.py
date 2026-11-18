"""F02: negative regression for full three-arm Tier-2 evidence binding.

All tests are local. No model, exchange, or upstream network calls occur.
"""
import hashlib
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / "source", ROOT / "source" / "v22_live"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import tier2 as T2
import tier2_integrity as I
import tier2_label_watch as LW


class BindingFixtureTests(unittest.TestCase):
    NOW = 1_800_000_000_000

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.con = T2.open_tier2(self.root / "tier2.sqlite")
        self.addCleanup(self.con.close)
        self.att = self.root / "attestation.jsonl"
        self.event = {
            "event_id": "d" * 64,
            "cohort": "geopolitics",
            "published_ms": self.NOW - 120_000,
            "observed_ms": self.NOW - 60_000,
        }
        T2.mock_triple(con=self.con, event=self.event,
                       now_ms=self.NOW, attestation_path=self.att)

    def test_full_binding_is_initially_valid(self):
        bundle = I.verify_binding(self.con, self.event["event_id"])
        self.assertEqual(bundle["mode"], "offline_fixture")
        self.assertEqual(len(bundle["arms"]), 3)
        row = self.con.execute(
            "SELECT seal_sha256 FROM tier2_triples").fetchone()
        self.assertEqual(row[0], I.sha(bundle))

    def test_F02_changed_arm_values_are_detected(self):
        self.con.execute(
            "UPDATE tier2_arms SET p_up=0.01, expected_return_bps=-100 "
            "WHERE arm='news'")
        self.con.commit()
        with self.assertRaisesRegex(I.IntegrityError, "sealed_arm_changed"):
            I.verify_binding(self.con, self.event["event_id"])

    def test_F02_changed_triple_and_recomputed_legacy_sha_are_detected(self):
        self.con.execute("UPDATE tier2_triples SET entry_price=1")
        self.con.commit()
        with self.assertRaisesRegex(I.IntegrityError, "sealed_triple"):
            I.verify_binding(self.con, self.event["event_id"])

    def test_F02_deleted_chain_cannot_be_labelled(self):
        self.att.unlink()
        due = self.con.execute(
            "SELECT entry_observed_ms+horizon_ms FROM tier2_triples").fetchone()[0]
        called = []
        with self.assertRaisesRegex(LW.LabelWatchError, "sealed_evidence_invalid"):
            LW.settle(db_path=self.root / "tier2.sqlite",
                      now_ms=due + 120_000, dry=False,
                      candle_fn=lambda **kw: called.append(kw))
        self.assertEqual(called, [])
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM tier2_outcomes").fetchone()[0], 0)

    def test_F02_tampered_arm_cannot_be_labelled(self):
        self.con.execute("UPDATE tier2_arms SET uncertainty=0.999")
        self.con.commit()
        due = self.con.execute(
            "SELECT entry_observed_ms+horizon_ms FROM tier2_triples").fetchone()[0]
        called = []
        with self.assertRaisesRegex(LW.LabelWatchError, "sealed_evidence_invalid"):
            LW.settle(db_path=self.root / "tier2.sqlite",
                      now_ms=due + 120_000, dry=False,
                      candle_fn=lambda **kw: called.append(kw))
        self.assertEqual(called, [])


class RealWireBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.con = T2.open_tier2(self.root / "tier2.sqlite")
        self.addCleanup(self.con.close)
        self.trial = "f02-test-live"
        self.event = {
            "event_id": "e" * 64,
            "cohort": "geopolitics",
            "published_ms": int(time.time() * 1000) - 120_000,
            "observed_ms": int(time.time() * 1000) - 60_000,
        }
        self.prompts = {"news": "N", "news_market": "NM", "market": "M"}
        self.att = self.root / "attestation.jsonl"
        self.freeze_bytes = (json.dumps({
            "trial_id": self.trial, "prompts": self.prompts,
            "event": self.event,
            "decision_ms": int(time.time() * 1000),
            "cutoff_ms": self.event["published_ms"],
            "prompt_version": T2.PROMPTS_VERSION,
            "protocol_sha256": T2.protocol_sha(),
            "system_prompt": T2.ARM_SYSTEM_T2,
        }, sort_keys=True) + "\n").encode()
        (self.root / ("frozen-" + self.trial + ".json")).write_bytes(
            self.freeze_bytes)
        self.freeze_sha = hashlib.sha256(self.freeze_bytes).hexdigest()
        T2.open_attempts(self.con)
        T2.reserve_attempts(self.con, trial_id=self.trial, now_ms=1)
        self.answers = None

    @staticmethod
    def _response(idx):
        content = json.dumps({
            "p_up": 0.56, "expected_return_bps": 40,
            "uncertainty": 0.4, "rationale": "offline simulated reply " + str(idx),
        })
        return json.dumps({
            "id": "mock-response-" + str(idx), "model": "deepseek-v4-flash",
            "choices": [{"message": {"content": content},
                         "finish_reason": "stop"}],
        }).encode()

    def _call_three(self):
        calls = []
        class Resp:
            status = 200
            headers = {}
            def __init__(self, raw): self.raw = raw
            def read(self): return self.raw
        def fake_opener(_request, timeout=None):
            calls.append(1)
            return Resp(self._response(len(calls)))
        with patch.object(T2, "require_gate", return_value={
                "trial_id": self.trial, "max_model_invocations": 3}):
            out = T2.live_executor(
                model="deepseek-v4-flash", attestation_path=self.att,
                opener=fake_opener)(
                    self.prompts, now_ms=1, con=self.con, trial_id=self.trial)
        self.assertEqual(len(calls), 3)
        return out

    def _seal(self, answers):
        decision_ms = int(time.time() * 1000)
        last = max(a["completed_ms"] for a in answers)
        quote = {"price": 64000.0, "observed_ms": last + 1000,
                 "source": "binance_public_ticker", "symbol": "BTCUSDT"}
        return T2.seal_triple(
            con=self.con, event=self.event, arm_results=answers, quote=quote,
            attestation_path=self.att, now_ms=quote["observed_ms"] + 1000,
            decision_ms=decision_ms, trial_id=self.trial,
            frozen_sha256=self.freeze_sha,
            prompts_sha256=T2.canonical_sha(self.prompts))

    def test_F02_exact_three_live_responses_bound_to_frozen_prompts(self):
        answers = self._call_three()
        out = self._seal(answers)
        self.assertEqual(out["status"], "sealed")
        bundle = I.verify_binding(self.con, self.event["event_id"],
                                  require_live=True)
        self.assertEqual(bundle["mode"], "live_raw")
        self.assertEqual(bundle["trial_id"], self.trial)
        self.assertEqual(bundle["frozen_sha256"], self.freeze_sha)

    def test_F02_changed_text_before_seal_rejected(self):
        answers = self._call_three()
        answers[0]["text"] = json.dumps({
            "p_up": 0.01, "expected_return_bps": -100,
            "uncertainty": 0.5, "rationale": "tampered"})
        with self.assertRaisesRegex(I.IntegrityError,
                                    "response_text_not_raw_bound"):
            self._seal(answers)
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM tier2_triples").fetchone()[0], 0)

    def test_F02_missing_raw_after_seal_rejected(self):
        answers = self._call_three()
        self._seal(answers)
        (self.root / "raw_model_calls" / self.trial /
         "news.response.bin").unlink()
        with self.assertRaisesRegex(I.IntegrityError,
                                    "raw_evidence_invalid"):
            I.verify_binding(self.con, self.event["event_id"],
                             require_live=True)

    def test_F02_remaining_frozen_event_cannot_change_before_seal(self):
        answers = self._call_three()
        self.event["event_id"] = "f" * 64
        with self.assertRaisesRegex(I.IntegrityError,
                                    "frozen_event_triple_mismatch"):
            self._seal(answers)
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM tier2_triples").fetchone()[0], 0)

    def test_F02_remaining_rehashed_db_numbers_do_not_override_raw(self):
        answers = self._call_three()
        self._seal(answers)
        eid = self.event["event_id"]
        raw = self.con.execute(
            "SELECT bundle_json FROM tier2_bindings WHERE event_id=?",
            (eid,)).fetchone()[0]
        bundle = json.loads(raw)
        news = next(doc for doc in bundle["arms"]
                    if doc["arm"] == "news")
        news["p_up"] = 0.99   # raw still says 0.56
        sha = I.sha(bundle)
        self.con.execute(
            "UPDATE tier2_arms SET p_up=0.99 WHERE event_id=? AND arm='news'",
            (eid,))
        self.con.execute(
            "UPDATE tier2_bindings SET bundle_json=?,bundle_sha256=? "
            "WHERE event_id=?", (I.canonical(bundle), sha, eid))
        self.con.execute(
            "UPDATE tier2_triples SET seal_sha256=? WHERE event_id=?", (sha, eid))
        self.con.commit()
        with self.assertRaisesRegex(I.IntegrityError,
                                    "raw_forecast_numeric_mismatch"):
            I.verify_binding(self.con, eid, require_live=True)

    def test_F02_modified_frozen_prompt_rejected(self):
        answers = self._call_three()
        self._seal(answers)
        (self.root / ("frozen-" + self.trial + ".json")).write_text(
            '{"trial_id":"changed"}')
        with self.assertRaisesRegex(I.IntegrityError,
                                    "frozen_input_digest_mismatch"):
            I.verify_binding(self.con, self.event["event_id"],
                             require_live=True)


if __name__ == "__main__":
    unittest.main()
