#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import series_guard as g

class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = g.open_db(Path(self.temp.name) / "series.sqlite")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_dedup_and_no_overwrite(self):
        g.claim(self.conn, event_id="e1", semantic_cluster_id="c1", trial_id="t1", at_ms=1)
        with self.assertRaises(g.Refused):
            g.claim(self.conn, event_id="e2", semantic_cluster_id="c1", trial_id="t2", at_ms=2)
        self.assertEqual(g.counters(self.conn)["claimed"], 1)

    def test_three_calls_and_no_retry(self):
        g.claim(self.conn, event_id="e1", semantic_cluster_id="c1", trial_id="t1", at_ms=1)
        with self.assertRaises(g.Refused):
            g.reserve_model_call(self.conn, event_id="e1", arm="market", at_ms=2)
        for index, arm in enumerate(g.ARM_NAMES):
            self.assertEqual(g.reserve_model_call(self.conn, event_id="e1", arm=arm, at_ms=2), index+1)
            with self.assertRaises(g.Refused):
                g.reserve_model_call(self.conn, event_id="e1", arm=arm, at_ms=2)
            g.record_response(self.conn, event_id="e1", arm=arm, response_id="resp-" + arm)
        g.finalize(self.conn, event_id="e1", state="sealed")
        g.record_label(self.conn, event_id="e1", state="labelled", at_ms=3)
        with self.assertRaises(g.Refused):
            g.record_label(self.conn, event_id="e1", state="labelled", at_ms=4)
        self.assertEqual(g.counters(self.conn)["calls_reserved"], 3)

    def test_uncertain_call_consumes_slot(self):
        g.claim(self.conn, event_id="e1", semantic_cluster_id="c1", trial_id="t1", at_ms=1)
        g.reserve_model_call(self.conn, event_id="e1", arm="news", at_ms=2)
        g.finalize(self.conn, event_id="e1", state="unknown", reason="timeout")
        with self.assertRaises(g.Refused):
            g.reserve_model_call(self.conn, event_id="e1", arm="news_market", at_ms=3)
        self.assertEqual(g.counters(self.conn)["calls_reserved"], 1)

    def test_reject_incomplete_seal(self):
        g.claim(self.conn, event_id="e1", semantic_cluster_id="c1", trial_id="t1", at_ms=1)
        with self.assertRaises(g.Refused):
            g.finalize(self.conn, event_id="e1", state="sealed")

    def test_strict_activation(self):
        folder = Path(self.temp.name)
        act, gate = folder / "activation.json", folder / "GO"
        gate.write_text("go")
        record = {
            "purpose": g.SERIES_PURPOSE, "evidence_tier": g.EVIDENCE_TIER,
            "upstream_tool_isolation": "unverified", "paper_only": True,
            "max_model_invocations": 300, "max_events": 100, "owner_go": True,
            "owner_seq": 500, "series_id": "multi100",
            "started_ms": 1000, "expires_ms": None,
            "no_calendar_expiry": True
        }
        act.write_text(json.dumps(record))
        self.assertEqual(g.validate_activation(activation_path=act, gate_path=gate,
                                              current_ms=1200)["series_id"], "multi100")
        self.assertEqual(g.validate_activation(activation_path=act, gate_path=gate,
                                              current_ms=3000000)["series_id"], "multi100")
        record["expires_ms"] = 4000
        act.write_text(json.dumps(record))
        with self.assertRaises(g.Refused):
            g.validate_activation(activation_path=act, gate_path=gate, current_ms=1200)
        record["expires_ms"] = None
        record["max_model_invocations"] = 301
        act.write_text(json.dumps(record))
        with self.assertRaises(g.Refused):
            g.validate_activation(activation_path=act, gate_path=gate, current_ms=1200)

    def test_300_call_budget(self):
        for i in range(100):
            e = "e" + str(i)
            g.claim(self.conn, event_id=e, semantic_cluster_id="c" + str(i),
                    trial_id="t" + str(i), at_ms=1)
            for arm in g.ARM_NAMES:
                g.reserve_model_call(self.conn, event_id=e, arm=arm, at_ms=2)
                g.record_response(self.conn, event_id=e, arm=arm, response_id="r")
            g.finalize(self.conn, event_id=e, state="aborted", reason="synthetic")
        self.assertEqual(g.counters(self.conn)["calls_reserved"], 300)
        with self.assertRaises(g.Refused):
            g.claim(self.conn, event_id="e101", semantic_cluster_id="c101",
                    trial_id="t101", at_ms=3)

if __name__ == "__main__":
    unittest.main()
