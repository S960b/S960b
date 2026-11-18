"""ChatGPT-authored tests for the disabled-by-default Tier-2 one-shot driver."""
import hashlib
import json
import sqlite3
from email.utils import formatdate
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "source", ROOT / "source" / "v22_live"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import tier2_forward_driver as D
import v24_collector as COL


class CandidateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "events.sqlite"
        self.raw = self.root / "feed.bin"
        self.now = 1_800_000_000_000
        self.pub = self.now - 45_000
        self.obs = self.now - 20_000
        self.title = "Official new release"
        self.url = "https://www.whitehouse.gov/example/"
        self.event_id = COL.occurrence_id("whitehouse_actions",
                                          self.title, self.pub)
        pub_date = formatdate(self.pub / 1000, usegmt=True)
        self.body = (f"<rss version='2.0'><channel><item>"
                     f"<title>{self.title}</title><link>{self.url}</link>"
                     f"<pubDate>{pub_date}</pubDate>"
                     f"</item></channel></rss>").encode()
        self.raw.write_bytes(self.body)
        self.sha = hashlib.sha256(self.body).hexdigest()
        self.con = sqlite3.connect(str(self.db))
        self.addCleanup(self.con.close)
        self.con.executescript("""
            CREATE TABLE v24_events(
              content_sha256 TEXT, source_id TEXT, category TEXT,
              provenance TEXT, title TEXT, origin_url TEXT,
              published_ms INTEGER, observed_ms INTEGER,
              raw_sha256 TEXT);
            CREATE TABLE v24_observations(
              occurrence_id TEXT, raw_path TEXT,
              raw_sha256 TEXT, observed_ms INTEGER,
              title TEXT, origin_url TEXT, published_ms INTEGER,
              source_id TEXT);
            CREATE TABLE v24_raw_manifest(
              raw_sha256 TEXT, path TEXT, http_status INTEGER,
              source_id TEXT, observed_ms INTEGER, url TEXT);
        """)
        self.con.execute(
            "INSERT INTO v24_events VALUES(?,?,?,?,?,?,?,?,?)",
            (self.event_id, "whitehouse_actions", "geopolitics", "primary",
             self.title, self.url, self.pub, self.obs, self.sha))
        self.con.execute(
            "INSERT INTO v24_observations VALUES(?,?,?,?,?,?,?,?)",
            (self.event_id, str(self.raw), self.sha, self.obs,
             self.title, self.url, self.pub, "whitehouse_actions"))
        self.con.execute(
            "INSERT INTO v24_raw_manifest VALUES(?,?,?,?,?,?)",
            (self.sha, str(self.raw), 200, "whitehouse_actions", self.obs,
             "https://www.whitehouse.gov/presidential-actions/feed/"))
        self.con.commit()

    def test_fresh_verified_candidate(self):
        e = D.candidate(self.con, self.now)
        self.assertEqual(e["event_id"], self.event_id)
        self.assertEqual(e["observed_ms"], self.obs)
        self.assertEqual(e["summary"], "")

    def test_old_news_is_not_promoted_by_recent_fetch(self):
        self.con.execute(
            "UPDATE v24_events SET published_ms=?",
            (self.now - 700_000,))
        self.con.commit()
        self.assertIsNone(D.candidate(self.con, self.now))

    def test_future_news_is_never_selected(self):
        self.con.execute(
            "UPDATE v24_events SET published_ms=?",
            (self.now + 10_000,))
        self.con.commit()
        self.assertIsNone(D.candidate(self.con, self.now))

    def test_historical_event_cannot_borrow_later_raw_archive(self):
        # Re-fetching an old RSS body does not prove it was retained when the
        # original event was first collected; timestamps must match exactly.
        self.con.execute("UPDATE v24_raw_manifest SET observed_ms=?",
                         (self.obs + 180_000,))
        self.con.commit()
        self.assertIsNone(D.candidate(self.con, self.now))

    def test_mismatched_archive_source_is_not_eligible(self):
        self.con.execute("UPDATE v24_raw_manifest SET source_id='other'")
        self.con.commit()
        self.assertIsNone(D.candidate(self.con, self.now))

    def test_mismatched_observation_digest_is_not_eligible(self):
        self.con.execute("UPDATE v24_observations SET raw_sha256=?",
                         ("0" * 64,))
        self.con.commit()
        self.assertIsNone(D.candidate(self.con, self.now))

    def test_F05_forged_title_with_recomputed_occurrence_id_is_refused(self):
        forged = "Forged presidential action"
        forged_id = COL.occurrence_id("whitehouse_actions", forged, self.pub)
        self.con.execute("UPDATE v24_events SET content_sha256=?, title=?",
                         (forged_id, forged))
        self.con.execute(
            "UPDATE v24_observations SET occurrence_id=?, title=?",
            (forged_id, forged))
        self.con.commit()
        self.assertIsNone(D.candidate(self.con, self.now))

    def test_F05_forged_url_with_matching_db_metadata_is_refused(self):
        forged_url = "https://www.whitehouse.gov/not-in-rss/"
        self.con.execute("UPDATE v24_events SET origin_url=?", (forged_url,))
        self.con.execute("UPDATE v24_observations SET origin_url=?",
                         (forged_url,))
        self.con.commit()
        self.assertIsNone(D.candidate(self.con, self.now))

    def test_F05_forged_date_and_identity_refused(self):
        new_pub = self.pub - 25_000
        forged_id = COL.occurrence_id("whitehouse_actions",
                                     self.title, new_pub)
        self.con.execute(
            "UPDATE v24_events SET content_sha256=?, published_ms=?",
            (forged_id, new_pub))
        self.con.execute(
            "UPDATE v24_observations SET occurrence_id=?, published_ms=?",
            (forged_id, new_pub))
        self.con.commit()
        self.assertIsNone(D.candidate(self.con, self.now))

    def test_F05_wrong_feed_url_is_refused(self):
        self.con.execute(
            "UPDATE v24_raw_manifest SET url='https://example.invalid/other'")
        self.con.commit()
        self.assertIsNone(D.candidate(self.con, self.now))

    def test_F05_nonmatching_rss_item_rejected_even_with_matching_sha(self):
        other = self.body.replace(b"Official new release", b"Other genuine title")
        other_sha = hashlib.sha256(other).hexdigest()
        self.raw.write_bytes(other)
        self.con.execute("UPDATE v24_events SET raw_sha256=?", (other_sha,))
        self.con.execute("UPDATE v24_observations SET raw_sha256=?",
                         (other_sha,))
        self.con.execute("UPDATE v24_raw_manifest SET raw_sha256=?",
                         (other_sha,))
        self.con.commit()
        self.assertIsNone(D.candidate(self.con, self.now))

    def test_missing_raw_is_not_eligible(self):
        self.raw.unlink()
        with self.assertRaises(D.DriverError):
            D.candidate(self.con, self.now)

    def test_bad_digest_is_never_eligible(self):
        self.raw.write_bytes(b"tampered")
        with self.assertRaises(D.DriverError):
            D.candidate(self.con, self.now)

    def test_freeze_collision_is_named_and_does_not_call_model(self):
        trial = "once-001"
        path = self.root / ("frozen-" + trial + ".json")
        path.write_bytes(b"immutable-before-trial")
        prompts = {"news": "N", "news_market": "NM", "market": "M"}
        with patch.object(D.TR, "require_enabled", return_value={"trial_id": trial}), \
             patch.object(D.T2, "DATA", self.root), \
             patch.object(D.P, "fetch_market", return_value={"candle_count": 26}), \
             patch.object(D.A, "audit_market_payload"), \
             patch.object(D.A, "build_prompts_3", return_value=prompts), \
             patch.object(D.A, "audit_market_arm_isolation"), \
             patch.object(D.T2, "open_tier2", side_effect=AssertionError("no_db")), \
             patch.object(D.T2, "live_executor", side_effect=AssertionError("no_model")):
            result = D.execute_once(feed_db=self.db, now_ms=self.now, dry=False)
        self.assertEqual(result["status"], "freeze_already_exists")
        self.assertEqual(result["freeze_sha256"], hashlib.sha256(
            path.read_bytes()).hexdigest())

    def test_F01_stale_after_slow_market_fetch_calls_no_model(self):
        # The first wall clock admits the news, but the decision clock after
        # market data retrieval is beyond its 600-second freshness budget.
        prompts = {"news": "N", "news_market": "NM", "market": "M"}
        with patch.object(D, "_wall_ms", side_effect=[self.now,
                                                      self.now + 610_000]), \
             patch.object(D.TR, "require_enabled",
                          return_value={"trial_id": "trial-f01"}), \
             patch.object(D.T2, "DATA", self.root), \
             patch.object(D.P, "fetch_market", return_value={"candle_count": 26}), \
             patch.object(D.A, "audit_market_payload"), \
             patch.object(D.A, "build_prompts_3", return_value=prompts), \
             patch.object(D.A, "execution_metadata", return_value={}), \
             patch.object(D.A, "audit_market_arm_isolation"), \
             patch.object(D.T2, "open_tier2",
                          side_effect=AssertionError("no_trial_db")), \
             patch.object(D.T2, "live_executor",
                          side_effect=AssertionError("no_model")):
            with self.assertRaisesRegex(D.T2.Tier2Error, "event_too_old"):
                D.execute_once(feed_db=self.db, dry=False)
        self.assertEqual(list(self.root.glob("frozen-*.json")), [])

    def test_preflight_never_calls_a_model_or_writes_tier2(self):
        out = D.execute_once(feed_db=self.db, now_ms=self.now, dry=True)
        self.assertEqual(out["status"], "eligible_observed")
        self.assertEqual(out["model_calls"], 0)


class EvidenceTest(unittest.TestCase):
    def test_freeze_is_write_once(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "snapshot.json"
            sha = D._write_new_json(path, {"hello": "world"})
            self.assertEqual(sha, hashlib.sha256(path.read_bytes()).hexdigest())
            with self.assertRaises(FileExistsError):
                D._write_new_json(path, {"hello": "tampered"})
            self.assertEqual(json.loads(path.read_text())["hello"], "world")

    def test_label_failure_classification_is_explicit(self):
        self.assertEqual(D._label_failure_kind(D.P.PrepError(
            "label_pending_candle_open")), "pending_retry")
        self.assertEqual(D._label_failure_kind(D.P.PrepError(
            "label_fetch_failed:URLError")), "pending_retry")
        self.assertEqual(D._label_failure_kind(D.P.PrepError(
            "label_row_bad_bounds")), "error")
        self.assertEqual(D._label_failure_kind(ValueError("bug")), "error")

    def test_missing_tier2_db_does_not_create_it(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "tier2.sqlite"
            self.assertEqual(D.label_once(db_path=db)["status"], "no_tier2_db")
            self.assertFalse(db.exists())


if __name__ == "__main__":
    unittest.main()
