"""NEWS v2.4 stage 8 — offline integration acceptance tests. No network."""
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent if (HERE.parent / 'source').is_dir() or \
    (HERE.parent / 'v24_acceptance.py').is_file() else HERE
for _cand in (ROOT / 'source', ROOT, HERE):
    if (_cand / 'v24_acceptance.py').is_file():
        sys.path.insert(0, str(_cand))
        break

import v24_acceptance as AC
import v24_sources as S


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = AC.run()

    def test_accounting_balances(self):
        acc = self.report["accounting"]
        self.assertTrue(acc["balances_selection"])
        self.assertTrue(acc["balances_records"])
        self.assertEqual(acc["input"], acc["invalid"] + acc["accepted"]
                         + acc["rejected"])

    def test_every_accepted_story_is_recorded_and_labelled(self):
        acc = self.report["accounting"]
        self.assertEqual(acc["accepted"], acc["recorded"])
        self.assertEqual(acc["recorded"], acc["labelled"] + acc["unlabelled"])

    def test_export_counts_are_three_arms_per_triple(self):
        counts = self.report["export_counts"]
        self.assertEqual(counts["arms"], counts["triples"] * 3)
        self.assertEqual(counts["outcomes"], counts["triples"])

    def test_coverage_separates_story_from_availability(self):
        cell = self.report["coverage"]["timeline"][0]["categories"]["crypto"]
        self.assertIn("story", cell)
        self.assertIn("availability", cell)
        self.assertLessEqual(cell["story"]["coverage"], 1.0)
        self.assertLessEqual(cell["availability"]["uptime"], 1.0)

    def test_claims_block_denies_provenance_and_performance(self):
        claims = self.report["claims"]
        self.assertIn("NOT established", claims["provenance"])
        self.assertIn("no evidence", claims["performance"])

    def test_run_is_reproducible(self):
        again = AC.run()
        self.assertEqual(again["accounting"], self.report["accounting"])
        self.assertEqual(again["export_counts"], self.report["export_counts"])

    def test_no_network_in_acceptance_module(self):
        candidates = [ROOT / "v24_acceptance.py", HERE / "v24_acceptance.py",
                      ROOT / "source" / "v24_acceptance.py",
                      HERE / "source" / "v24_acceptance.py"]
        path = next((c for c in candidates if c.is_file()), None)
        self.assertIsNotNone(path)
        text = path.read_text(encoding="utf-8")
        for banned in ("import urllib", "import requests", "import socket",
                       "urlopen(", "requests.get"):
            self.assertNotIn(banned, text)


class HeartbeatSemanticsTests(unittest.TestCase):
    """P0-1: a story is not a heartbeat; a quiet feed is not an outage."""

    def _reg(self):
        return S.SourceRegistry(
            [S.SourceSpec(f"{c}-one", c, "primary", 900) for c in S.CATEGORIES])

    def test_story_without_probe_is_unknown(self):
        now = 1_800_000_000_000
        rec = S.SourceRecord("crypto-one", "crypto", "primary", now - 5_000,
                             now - 2_000, "A story with enough words",
                             "https://example.com/x",
                             S.content_digest("A story with enough words",
                                              now - 5_000))
        out = S.coverage_timeline([rec], start_ms=now - 3_600_000,
                                  end_ms=now + 1, bucket_ms=3_600_000,
                                  registry=self._reg())
        cell = out["timeline"][0]["categories"]["crypto"]
        self.assertEqual(cell["availability"]["status"], "unknown")
        self.assertEqual(cell["story"]["status"], "ok")

    def test_unknown_source_is_quarantined_and_cannot_inflate_uptime(self):
        now = 1_800_000_000_000
        ghost = S.SourceRecord("ghost", "crypto", "primary", now - 5_000,
                               now - 2_000, "Ghost story here",
                               "https://example.com/y",
                               S.content_digest("Ghost story here", now - 5_000))
        out = S.coverage_timeline([ghost], start_ms=now - 3_600_000,
                                  end_ms=now + 1, bucket_ms=3_600_000,
                                  registry=self._reg())
        cell = out["timeline"][0]["categories"]["crypto"]
        self.assertEqual(cell["story"]["sources_with_story"], 0)
        self.assertLessEqual(cell["story"]["coverage"], 1.0)
        self.assertEqual(out["quarantined"][0]["reason"], "unknown_source:ghost")

    def test_future_ingestion_is_quarantined(self):
        now = 1_800_000_000_000
        rec = S.SourceRecord("crypto-one", "crypto", "primary", now - 5_000,
                             now + 60_000, "A future story item",
                             "https://example.com/z",
                             S.content_digest("A future story item", now - 5_000))
        out = S.coverage_timeline([rec], start_ms=now - 3_600_000,
                                  end_ms=now + 1, bucket_ms=3_600_000,
                                  registry=self._reg(), cutoff_ms=now)
        self.assertEqual(out["quarantined"][0]["reason"], "ingested_after_cutoff")


if __name__ == "__main__":
    unittest.main()
