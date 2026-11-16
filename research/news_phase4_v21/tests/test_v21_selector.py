"""NEWS v2.1 — event selection tests (offline, temp DB, no network/model).

Covers cohort separation, BTC relevance filtering, cross-outlet dedupe,
priority ordering (fast_verified before context_news, primary before media),
and the 2 h decision window.
"""
import json
import os
import sqlite3
import tempfile
import time
import unittest

import v21_selector as S

NOW = 1_800_000_000_000
SCHEMA = """CREATE TABLE events(
 event_id TEXT PRIMARY KEY, source TEXT NOT NULL, origin_id TEXT NOT NULL,
 revision TEXT NOT NULL, published_ms INTEGER, observed_ms INTEGER NOT NULL,
 fast_eligible INTEGER NOT NULL, source_kind TEXT NOT NULL,
 content_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL, cohort TEXT);"""


def make_db(rows):
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    for i, r in enumerate(rows):
        payload = json.dumps({"title": r["title"], "summary": r.get("summary", ""),
                              "published_ms": r["published_ms"],
                              "observed_ms": r["observed_ms"]})
        con.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (r.get("id") or f"{i:064d}", r["source"],
                     r.get("origin", f"o{i}"), "v1", r["published_ms"],
                     r["observed_ms"], int(r.get("fast", 0)),
                     r.get("kind", "secondary"), "x" * 64, payload,
                     r["cohort"]))
    con.commit()
    con.close()
    return path, con


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.paths = []

    def tearDown(self):
        for p in self.paths:
            for suf in ("", "-wal", "-shm"):
                try:
                    os.unlink(p + suf)
                except FileNotFoundError:
                    pass

    def _con(self, rows):
        path, _ = make_db(rows)
        self.paths.append(path)
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.execute("PRAGMA query_only=ON")
        self.addCleanup(con.close)
        return con

    def test_no_events_returns_empty(self):
        chosen, decisions = S.select(self._con([]), NOW)
        self.assertEqual(chosen, [])
        self.assertEqual(decisions, [])

    def test_stale_fast_item_is_demoted_not_promoted(self):
        # Ingested as fast_verified, but 30 minutes old at DECISION time.
        con = self._con([
            {"source": "sec", "title": "SEC approves Bitcoin ETF rule change",
             "published_ms": NOW - 1800_000, "observed_ms": NOW - 90_000,
             "cohort": "fast_verified", "kind": "primary", "fast": 1},
        ])
        chosen, decisions = S.select(con, NOW)
        self.assertEqual(len(chosen), 1, "still eligible as context news")
        self.assertEqual(chosen[0]["cohort"], "context_news")
        self.assertEqual(chosen[0]["declared_cohort"], "fast_verified")
        self.assertEqual(chosen[0]["cohort_note"], "demoted_from_fast")

    def test_fast_item_past_two_hours_is_dropped_entirely(self):
        con = self._con([
            {"source": "sec", "title": "SEC approves Bitcoin ETF rule change",
             "published_ms": NOW - 3 * 3_600_000,
             "observed_ms": NOW - 100_000,
             "cohort": "fast_verified", "kind": "primary", "fast": 1},
        ])
        chosen, decisions = S.select(con, NOW)
        self.assertEqual(chosen, [])
        self.assertEqual(decisions, [], "outside every window, not loaded")

    def test_fresh_fast_item_stays_fast(self):
        con = self._con([
            {"source": "sec", "title": "SEC approves Bitcoin ETF rule change",
             "published_ms": NOW - 60_000, "observed_ms": NOW - 30_000,
             "cohort": "fast_verified", "kind": "primary", "fast": 1},
        ])
        chosen, _ = S.select(con, NOW)
        self.assertEqual(chosen[0]["cohort"], "fast_verified")
        self.assertIsNone(chosen[0].get("cohort_note"))

    def test_future_publish_time_is_rejected(self):
        con = self._con([
            {"source": "coindesk", "title": "Bitcoin ETF inflows record",
             "published_ms": NOW + 600_000, "observed_ms": NOW,
             "cohort": "context_news"},
        ])
        chosen, _ = S.select(con, NOW)
        self.assertEqual(chosen, [])

    def test_fast_verified_beats_context(self):
        con = self._con([
            {"source": "coindesk", "title": "Bitcoin ETF inflows hit record",
             "published_ms": NOW - 900_000, "observed_ms": NOW - 840_000,
             "cohort": "context_news"},
            {"source": "sec", "title": "SEC approves Bitcoin ETF rule change",
             "published_ms": NOW - 60_000, "observed_ms": NOW - 30_000,
             "cohort": "fast_verified", "kind": "primary", "fast": 1},
        ])
        chosen, _ = S.select(con, NOW)
        self.assertEqual(chosen[0]["cohort"], "fast_verified")

    def test_primary_beats_secondary_within_same_cohort(self):
        con = self._con([
            {"source": "coindesk", "title": "Bitcoin market crashes hard today",
             "published_ms": NOW - 60_000, "observed_ms": NOW - 30_000,
             "cohort": "context_news"},
            {"source": "fed", "title": "Federal Reserve issues FOMC statement on rates",
             "published_ms": NOW - 120_000, "observed_ms": NOW - 60_000,
             "cohort": "fast_verified", "kind": "primary", "fast": 1},
        ])
        chosen, _ = S.select(con, NOW)
        self.assertEqual(chosen[0]["source"], "fed")

    def test_irrelevant_story_is_declined_not_selected(self):
        con = self._con([
            {"source": "kraken_status", "title": "Kava (KAVA) Funding Delays",
             "published_ms": NOW - 60_000, "observed_ms": NOW - 20_000,
             "cohort": "fast_verified", "kind": "primary", "fast": 1},
        ])
        chosen, decisions = S.select(con, NOW)
        self.assertEqual(chosen, [])
        self.assertEqual(decisions[0]["decision"], "declined")
        self.assertIn("not_btc_relevant", decisions[0]["reasons"])

    def test_story_older_than_two_hours_is_out_of_window(self):
        con = self._con([
            {"source": "coindesk", "title": "Bitcoin old news from earlier",
             "published_ms": NOW - 4 * 3_600_000,
             "observed_ms": NOW - 4 * 3_600_000 + 60_000,
             "cohort": "context_news"},
        ])
        chosen, decisions = S.select(con, NOW)
        self.assertEqual(chosen, [])
        self.assertEqual(decisions, [])

    def test_duplicate_titles_across_outlets_collapse(self):
        a = "Bitcoin ETF outflows hit a record high this week"
        con = self._con([
            {"source": "coindesk", "title": a,
             "published_ms": NOW - 120_000, "observed_ms": NOW - 60_000,
             "cohort": "context_news"},
            {"source": "theblock", "title": a,
             "published_ms": NOW - 110_000, "observed_ms": NOW - 55_000,
             "cohort": "context_news"},
        ])
        chosen, decisions = S.select(con, NOW)
        declined = [d for d in decisions if d["decision"] == "declined"]
        self.assertEqual(len(declined), 1)
        self.assertTrue(any(r.startswith("duplicate_of_")
                            for r in declined[0]["reasons"]))
        self.assertEqual(len(chosen), 1)

    def test_history_only_cohort_never_selected(self):
        con = self._con([
            {"source": "bls", "title": "Bitcoin mining payrolls monthly data",
             "published_ms": NOW - 3_600_000, "observed_ms": NOW - 60_000,
             "cohort": "history_only", "kind": "primary"},
        ])
        chosen, _ = S.select(con, NOW)
        self.assertEqual(chosen, [])

    def test_limit_respected(self):
        # Deliberately distinct titles, otherwise the dedupe layer (correctly)
        # collapses near-identical headlines before the limit applies.
        titles = ["Bitcoin ETF inflows record", "Ethereum upgrade ships",
                  "Coinbase custody expansion", "Kraken listing new token",
                  "Stablecoin supply shrinks"]
        rows = [{"source": "coindesk", "title": titles[i],
                 "published_ms": NOW - 100_000 - i * 1000,
                 "observed_ms": NOW - 50_000 - i * 1000,
                 "cohort": "context_news", "origin": f"o{i}"} for i in range(5)]
        chosen, _ = S.select(self._con(rows), NOW, limit=2)
        self.assertEqual(len(chosen), 2)

    def test_near_identical_headlines_collapse_before_limit(self):
        rows = [{"source": "coindesk",
                 "title": f"Bitcoin exchange update number {i} today",
                 "published_ms": NOW - 100_000 - i * 1000,
                 "observed_ms": NOW - 50_000 - i * 1000,
                 "cohort": "context_news", "origin": f"o{i}"} for i in range(5)]
        chosen, decisions = S.select(self._con(rows), NOW, limit=2)
        self.assertEqual(len(chosen), 1)
        self.assertTrue(any("duplicate_of_" in r
                            for d in decisions for r in d.get("reasons", [])))

    def test_selection_is_deterministic(self):
        rows = [{"source": "coindesk", "title": "Bitcoin news item alpha",
                 "published_ms": NOW - 120_000, "observed_ms": NOW - 60_000,
                 "cohort": "context_news"},
                {"source": "decrypt", "title": "Bitcoin news item beta",
                 "published_ms": NOW - 110_000, "observed_ms": NOW - 55_000,
                 "cohort": "context_news"}]
        con = self._con(rows)
        first = [c["event_id"] for c in S.select(con, NOW, limit=2)[0]]
        second = [c["event_id"] for c in S.select(con, NOW, limit=2)[0]]
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
