"""NEWS v2.1 stage 2 — data integrity regressions (D06, D09, D10, D17).

Each test names the defect it pins and states the pre-fix behaviour it replaces.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Two layouts: flat (tests next to the modules) and published (tests/ beside
# source/). Parent, not parent.parent: the live tree has no nesting at all.
ROOT = HERE.parent if (HERE.parent / 'source').is_dir() or \
    (HERE.parent / 'v21_selector.py').is_file() else HERE
# The selector lives flat in the live tree and in source/ in the published one.
# Search, do not assume: the first directory that actually holds it wins.
# Import path: the modules live flat in the live tree and in source/ here.
for _cand in (ROOT / 'source', ROOT, ROOT / 'scripts', HERE):
    if (_cand / 'v21_selector.py').is_file():
        sys.path.insert(0, str(_cand))
        break

import v21_selector as S

PY = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable
# The export script lives in scripts/ in the published tree and flat next to the
# modules in the live tree; resolve whichever exists.
# scripts/ is canonical: the live tree also keeps a flat legacy copy that is NOT
# the one deployed, and preferring it silently tested the wrong file.
_CANDS = [ROOT / "scripts" / "v21_export.py", HERE / "scripts" / "v21_export.py",
          ROOT / "v21_export.py", HERE / "v21_export.py"]
EXPORT = next(c for c in _CANDS if c.is_file())
PAY = "https://example.test/story"

NOW = 1_800_000_000_000
# Minimal event table, exactly the columns the exporter reads.
SCHEMA = """CREATE TABLE events(
 event_id TEXT PRIMARY KEY, source TEXT NOT NULL, origin_id TEXT NOT NULL,
 published_ms INTEGER, observed_ms INTEGER NOT NULL,
 source_kind TEXT NOT NULL, payload_json TEXT NOT NULL, cohort TEXT);"""

# Selector fixture: the full ingestion schema, which has extra NOT NULL columns.
SELECTOR_SCHEMA = """CREATE TABLE events(
 event_id TEXT PRIMARY KEY, source TEXT NOT NULL, origin_id TEXT NOT NULL,
 revision TEXT NOT NULL, published_ms INTEGER, observed_ms INTEGER NOT NULL,
 fast_eligible INTEGER NOT NULL, source_kind TEXT NOT NULL,
 content_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL, cohort TEXT);"""


def make_events(rows):
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    con = sqlite3.connect(path)
    con.executescript(SELECTOR_SCHEMA)
    for i, r in enumerate(rows):
        payload = json.dumps({"title": r["title"],
                              "summary": r.get("summary", ""),
                              **r.get("payload", {})})
        con.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (r.get("id") or f"{i:064d}", r["source"],
                     r.get("origin", f"o{i}"), "v1", r["published_ms"],
                     r["observed_ms"], int(r.get("fast", 0)),
                     r.get("kind", "secondary"), "x" * 64, payload,
                     r["cohort"]))
    con.commit()
    con.close()
    return path


class DedupeOrderTests(unittest.TestCase):
    """D09: dedupe used to run BEFORE ranking, so the first variant seen won
    and a secondary mirror could suppress a primary source."""

    def setUp(self):
        self.paths = []

    def tearDown(self):
        for p in self.paths:
            for suf in ("", "-wal", "-shm"):
                try:
                    os.unlink(p + suf)
                except FileNotFoundError:
                    pass

    def _select(self, rows, limit=1):
        path = make_events(rows)
        self.paths.append(path)
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.execute("PRAGMA query_only=ON")
        try:
            return S.select(con, NOW, limit=limit)
        finally:
            con.close()

    def test_primary_source_beats_earlier_secondary_duplicate(self):
        rows = [
            # Secondary mirror is observed FIRST - under the old order it owned
            # the dedupe key and the primary source was declined as a duplicate.
            {"id": "a" * 64, "source": "coindesk", "kind": "media",
             "title": "Bitcoin price jumps after ETF approval",
             "published_ms": NOW - 60_000, "observed_ms": NOW - 40_000,
             "cohort": "context_news"},
            {"id": "b" * 64, "source": "sec", "kind": "primary",
             "title": "Bitcoin price jumps after ETF approval",
             "published_ms": NOW - 50_000, "observed_ms": NOW - 30_000,
             "cohort": "context_news"},
        ]
        chosen, decisions = self._select(rows, limit=1)
        self.assertEqual(len(chosen), 1)
        self.assertEqual(chosen[0]["source"], "sec",
                         "the primary source must win after ranking")
        declined = [d for d in decisions if d["decision"] == "declined"]
        self.assertEqual([d["event_id"] for d in declined], ["a" * 64])
        self.assertEqual(declined[0]["superseded_by"], "b" * 64)

    def test_one_event_one_pair_even_when_limit_is_large(self):
        # Four content words per title: dedupe_key keeps words longer than two
        # characters, so a shorter headline would produce no key at all.
        rows = [
            {"id": "c" * 64, "source": "coindesk",
             "title": "Bitcoin price jumps after ETF approval",
             "published_ms": NOW - 60_000, "observed_ms": NOW - 40_000,
             "cohort": "context_news"},
            {"id": "d" * 64, "source": "cointelegraph",
             "title": "Bitcoin price jumps after ETF approval",
             "published_ms": NOW - 50_000, "observed_ms": NOW - 30_000,
             "cohort": "context_news"},
        ]
        chosen, _ = self._select(rows, limit=10)
        self.assertEqual(len(chosen), 1,
                         "two mirrors of one story must never fill two slots")

    def test_tie_break_is_deterministic(self):
        # Identical rank components; only the event id differs.
        rows = [
            {"id": "e" * 64, "source": "bls", "kind": "primary",
             "title": "Bitcoin same headline", "published_ms": NOW - 60_000,
             "observed_ms": NOW - 55_000, "cohort": "context_news"},
            {"id": "0" * 64, "source": "bls", "kind": "primary",
             "title": "Bitcoin other headline", "published_ms": NOW - 60_000,
             "observed_ms": NOW - 55_000, "cohort": "context_news"},
        ]
        first, _ = self._select(rows, limit=2)
        second, _ = self._select(rows, limit=2)
        self.assertEqual([e["event_id"] for e in first],
                         [e["event_id"] for e in second])
        self.assertEqual([e["event_id"] for e in first],
                         sorted(["e" * 64, "0" * 64]))


class ExportUrlTests(unittest.TestCase):
    """D10: the exporter read `url`/`link` only, so a payload carrying
    `origin_url` exported null and the story could not be verified."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="v21exp2-")
        con = sqlite3.connect(os.path.join(self.dir, "v21.sqlite"))
        con.executescript(SCHEMA)
        con.commit()
        con.close()
        for name in ("paper.sqlite", "dispatch.sqlite"):
            con = sqlite3.connect(os.path.join(self.dir, name))
            con.executescript("""
              CREATE TABLE paper_records(event_id TEXT PRIMARY KEY, cohort TEXT,
                source TEXT, published_ms INT, observed_ms INT, cutoff_ms INT,
                reserved_ms INT, completed_ms INT, provider_id TEXT,
                model_id TEXT, seal_sha256 TEXT, synthetic INT, state TEXT);
              CREATE TABLE paper_arms(event_id TEXT, arm TEXT, attempt_id TEXT,
                session_id TEXT, prompt_sha256 TEXT, p_up REAL,
                expected_return_bps REAL, uncertainty REAL, action TEXT,
                response_sha256 TEXT, completed_ms INT);
              CREATE TABLE paper_entries(event_id TEXT PRIMARY KEY, price REAL,
                observed_ms INT, source TEXT);
              CREATE TABLE paper_outcomes(event_id TEXT, horizon_h INT,
                exit_ms INT, price REAL, settled_ms INT, observed_ms INT);
              CREATE TABLE dispatch_reservations(attempt_id TEXT PRIMARY KEY,
                event_id TEXT, arm TEXT, reserved_ms INT, state TEXT,
                synthetic INT);
              CREATE TABLE dispatch_lease(id INT, holder TEXT, acquired_ms INT,
                expires_ms INT, synthetic INT);
            """)
            con.commit()
            con.close()
        self.out = os.path.join(self.dir, "export.json")

    def _run(self, payload):
        path = os.path.join(self.dir, "v21.sqlite")
        con = sqlite3.connect(path)
        con.execute("DELETE FROM events")
        con.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?)",
                    ("f" * 64, "sec", "o1", 1000, 2000, "primary",
                     json.dumps(payload), "context_news"))
        con.commit()
        con.close()
        proc = subprocess.run(
            [PY, str(EXPORT), "--data-dir", self.dir,
             "--gateway-db", os.path.join(self.dir, "absent.db"),
             "--out", self.out],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "PYTHONPATH": str(ROOT / "source")})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = json.loads(Path(self.out).read_text(encoding="utf-8"))
        return doc["events"]["rows"][0]

    def test_origin_url_is_preserved_with_provenance(self):
        row = self._run({"title": "t", "origin_url": PAY})
        self.assertEqual(row["url"], PAY)
        self.assertEqual(row["origin_url"], PAY)
        self.assertEqual(row["url_provenance"], "origin_url")

    def test_legacy_url_key_still_works(self):
        row = self._run({"title": "t", "url": PAY})
        self.assertEqual(row["url"], PAY)
        self.assertEqual(row["url_provenance"], "url")

    def test_missing_url_is_labelled_not_silent(self):
        row = self._run({"title": "t"})
        self.assertIsNone(row["url"])
        self.assertIsNone(row["origin_url"])
        self.assertEqual(row["url_provenance"], "absent")

    def test_non_string_url_is_reported(self):
        row = self._run({"title": "t", "origin_url": {"nested": True}})
        self.assertIsNone(row["url"])
        self.assertEqual(row["url_provenance"], "origin_url_not_a_string")


if __name__ == "__main__":
    unittest.main()
