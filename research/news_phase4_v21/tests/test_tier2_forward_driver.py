"""ChatGPT-authored tests for the disabled-by-default Tier-2 one-shot driver."""
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "source", ROOT / "source" / "v22_live"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import tier2_forward_driver as D


class CandidateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "events.sqlite"
        self.raw = self.root / "feed.bin"
        self.body = b"<rss>origin data</rss>"
        self.raw.write_bytes(self.body)
        self.sha = hashlib.sha256(self.body).hexdigest()
        self.now = 1_800_000_000_000
        self.con = sqlite3.connect(str(self.db))
        self.addCleanup(self.con.close)
        self.con.executescript("""
            CREATE TABLE v24_events(
              content_sha256 TEXT, source_id TEXT, category TEXT,
              provenance TEXT, title TEXT, origin_url TEXT,
              published_ms INTEGER, observed_ms INTEGER,
              raw_sha256 TEXT);
            CREATE TABLE v24_observations(
              occurrence_id TEXT, raw_path TEXT);
            CREATE TABLE v24_raw_manifest(
              raw_sha256 TEXT, path TEXT, http_status INTEGER);
        """)
        self.pub = self.now - 45_000
        self.obs = self.now - 20_000
        self.con.execute(
            "INSERT INTO v24_events VALUES(?,?,?,?,?,?,?,?,?)",
            ("event-1", "whitehouse_actions", "geopolitics", "primary",
             "Official new release", "https://www.whitehouse.gov/example/",
             self.pub, self.obs, self.sha))
        self.con.execute(
            "INSERT INTO v24_observations VALUES(?,?)",
            ("event-1", str(self.raw)))
        self.con.execute(
            "INSERT INTO v24_raw_manifest VALUES(?,?,?)",
            (self.sha, str(self.raw), 200))
        self.con.commit()

    def test_fresh_verified_candidate(self):
        e = D.candidate(self.con, self.now)
        self.assertEqual(e["event_id"], "event-1")
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

    def test_missing_raw_is_not_eligible(self):
        self.raw.unlink()
        with self.assertRaises(D.DriverError):
            D.candidate(self.con, self.now)

    def test_bad_digest_is_never_eligible(self):
        self.raw.write_bytes(b"tampered")
        with self.assertRaises(D.DriverError):
            D.candidate(self.con, self.now)

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

    def test_missing_tier2_db_does_not_create_it(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "tier2.sqlite"
            self.assertEqual(D.label_once(db_path=db)["status"], "no_tier2_db")
            self.assertFalse(db.exists())


if __name__ == "__main__":
    unittest.main()
