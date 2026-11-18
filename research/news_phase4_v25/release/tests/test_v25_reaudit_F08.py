"""F08 read-only Tier-2 analyzer: censored exclusion, integrity and PnL.

No network, live database, inference, or real trade can occur in these tests.
"""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import tier2 as T2
import tier2_analyze as A
import tier2_integrity as I
import v24_collector as COL

NOW = 1_800_000_000_000


class AnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "tier2.sqlite"
        self.con = T2.open_tier2(self.db)
        self.addCleanup(self.con.close)
        self.event_id = "a" * 64
        self.att = self.root / "attestation.jsonl"

    def seal_fixture(self):
        ev = {"event_id": self.event_id, "cohort": "geopolitics",
              "published_ms": NOW - 120_000, "observed_ms": NOW - 60_000}
        return T2.mock_triple(
            con=self.con, event=ev, now_ms=NOW, attestation_path=self.att,
            actions={"news": (0.75, 90.0, 0.3),
                     "news_market": (0.25, -90.0, 0.3),
                     "market": (0.5, 0.0, 0.5)})["due_ms"]

    def label(self, due, price=64640.0):
        self.con.execute(
            "INSERT INTO tier2_outcomes VALUES (?,?,?,?,?,?,?)",
            (self.event_id, due, price, due + 59_999,
             "binance_public_1m_kline", 59_999, "labelled"))
        self.con.commit()

    def analyze(self, **kw):
        return A.analyze(db_path=self.db, now_ms=NOW, **kw)

    def test_F08_absent_db_never_created(self):
        missing = self.root / "absent.sqlite"
        got = A.analyze(db_path=missing)
        self.assertEqual(got["status"], "no_tier2_db")
        self.assertFalse(missing.exists())

    def test_F08_empty_db_no_samples_no_writes(self):
        before = self.db.stat().st_mtime_ns
        got = self.analyze()
        self.assertEqual(got["counts"]["triples"], 0)
        self.assertEqual(got["arms_live"]["news"]["n"], 0)
        self.assertEqual(self.db.stat().st_mtime_ns, before)

    def test_F08_offline_fixture_not_reported_as_live(self):
        due = self.seal_fixture()
        self.label(due)
        got = self.analyze()
        self.assertEqual(got["counts"]["verified_offline_fixture"], 1)
        self.assertEqual(got["counts"]["labelled_offline_fixture"], 1)
        self.assertEqual(got["counts"]["labelled_live"], 0)
        self.assertEqual(got["arms_live"]["news"]["n"], 0)
        self.assertEqual(got["arms_offline_fixture_diagnostic"]["news"]["n"], 0)

    def test_F08_fixture_math_is_explicitly_separate(self):
        due = self.seal_fixture()
        self.label(due)
        got = self.analyze(include_offline_fixtures=True)
        self.assertEqual(got["arms_live"]["news"]["n"], 0)
        fake = got["arms_offline_fixture_diagnostic"]
        self.assertEqual(fake["news"]["n"], 1)
        self.assertAlmostEqual(fake["news"]["mean_net_bps"], 94.0, places=4)
        self.assertAlmostEqual(fake["news_market"]["mean_net_bps"], -106.0,
                               places=4)
        self.assertEqual(fake["market"]["mean_net_bps"], 0.0)
        self.assertAlmostEqual(fake["news"]["mean_brier"], (0.75 - 1)**2)
        self.assertEqual(got["counts"]["invalid"], 0)

    def test_F08_censored_is_not_a_negative_ten_thousand_bps(self):
        due = self.seal_fixture()
        self.con.execute(
            "INSERT INTO tier2_outcomes VALUES (?,?,?,?,?,?,?)",
            (self.event_id, due, 0.0, due, "unavailable", -1, "censored"))
        self.con.commit()
        got = self.analyze(include_offline_fixtures=True)
        self.assertEqual(got["counts"]["censored"], 1)
        self.assertEqual(got["counts"]["labelled_offline_fixture"], 0)
        self.assertEqual(got["arms_offline_fixture_diagnostic"]["news"]["n"], 0)

    def test_F08_pending_has_no_return(self):
        self.seal_fixture()
        got = self.analyze(include_offline_fixtures=True)
        self.assertEqual(got["counts"]["pending"], 1)
        self.assertEqual(got["arms_live"]["news"]["mean_net_bps"], None)

    def test_F08_tampered_arm_invalid_even_with_label(self):
        due = self.seal_fixture()
        self.label(due)
        self.con.execute("UPDATE tier2_arms SET p_up=0.01 WHERE arm='news'")
        self.con.commit()
        got = self.analyze(include_offline_fixtures=True)
        self.assertEqual(got["status"], "invalid_evidence_found")
        self.assertEqual(got["counts"]["invalid"], 1)
        self.assertEqual(got["arms_offline_fixture_diagnostic"]["news"]["n"], 0)

    def test_F08_deleted_attestation_invalid(self):
        due = self.seal_fixture()
        self.label(due)
        self.att.unlink()
        got = self.analyze(include_offline_fixtures=True)
        self.assertEqual(got["status"], "invalid_evidence_found")
        self.assertEqual(got["counts"]["invalid"], 1)

    def test_F08_mismatched_label_due_invalid(self):
        due = self.seal_fixture()
        self.label(due)
        self.con.execute(
            "UPDATE tier2_outcomes SET due_ms=due_ms+123")
        self.con.commit()
        got = self.analyze(include_offline_fixtures=True)
        self.assertEqual(got["status"], "invalid_evidence_found")
        self.assertEqual(got["counts"]["invalid"], 1)

    def test_F08_feed_transport_lag_diagnostics_only(self):
        feed = self.root / "feed.sqlite"
        con = sqlite3.connect(str(feed))
        con.execute("CREATE TABLE v24_events(category TEXT, "
                    "published_ms INTEGER, observed_ms INTEGER)")
        con.execute("INSERT INTO v24_events VALUES(?,?,?)",
                    ("monetary_policy", NOW - 500_000, NOW))
        con.execute("INSERT INTO v24_events VALUES(?,?,?)",
                    ("monetary_policy", NOW - 180_000, NOW))
        con.execute("INSERT INTO v24_events VALUES(?,?,?)",
                    ("regulatory", NOW - 1_000_000, NOW - 400_000))
        con.commit()
        con.close()
        before = feed.stat().st_mtime_ns
        got = self.analyze(feed_db=feed)
        lag = got["feed_lag_diagnostics"]
        self.assertTrue(lag["does_not_change_protocol"])
        self.assertEqual(lag["classification"], "overlapping_diagnostics_only")
        self.assertEqual(lag["by_category"]["monetary_policy"]
                         ["transport_lag_over_budget"], 1)
        self.assertEqual(lag["by_category"]["monetary_policy"]["total"], 2)
        self.assertEqual(feed.stat().st_mtime_ns, before)


if __name__ == "__main__":
    unittest.main()
