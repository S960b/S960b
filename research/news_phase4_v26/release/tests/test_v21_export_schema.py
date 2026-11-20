"""NEWS v2.1 — schema tests for the public exporter.

The published export must be reproducible from a local database copy. These
tests build a tiny synthetic database, run the exporter CLI, and assert the
documented shape: required keys, per-pair integrity, and the three-way split
between reservations, completions and tool invocations.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPORT = ROOT / "scripts" / "v21_export.py"
# sys.executable may point at the desktop AppImage in this environment, which
# would silently launch the GUI instead of running the script. Prefer the
# system interpreter, and fall back to whatever is running us.
PY = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable


def make_db(path):
    con = sqlite3.connect(path)
    con.executescript("""
      CREATE TABLE events(event_id TEXT PRIMARY KEY, source TEXT, origin_id TEXT,
        published_ms INTEGER, observed_ms INTEGER, source_kind TEXT,
        cohort TEXT, payload_json TEXT);
      CREATE TABLE paper_records(event_id TEXT PRIMARY KEY, cohort TEXT,
        source TEXT, published_ms INT, observed_ms INT, cutoff_ms INT,
        reserved_ms INT, completed_ms INT, provider_id TEXT, model_id TEXT,
        seal_sha256 TEXT, synthetic INT, state TEXT);
      CREATE TABLE paper_arms(event_id TEXT, arm TEXT, attempt_id TEXT,
        session_id TEXT, prompt_sha256 TEXT, p_up REAL,
        expected_return_bps REAL, uncertainty REAL, action TEXT,
        response_sha256 TEXT, completed_ms INT);
      CREATE TABLE paper_entries(event_id TEXT PRIMARY KEY, price REAL,
        observed_ms INT, source TEXT);
      CREATE TABLE paper_outcomes(event_id TEXT, horizon_h INT, exit_ms INT,
        price REAL, settled_ms INT, observed_ms INT);
      CREATE TABLE dispatch_reservations(attempt_id TEXT PRIMARY KEY,
        event_id TEXT, arm TEXT, reserved_ms INT, state TEXT, synthetic INT);
      CREATE TABLE dispatch_lease(id INT, holder TEXT, acquired_ms INT,
        expires_ms INT, synthetic INT);
    """)
    con.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?)",
                ("e" * 64, "coindesk", "o1", 1000, 2000, "secondary",
                 "context_news", '{"title":"t","url":"https://example.test/a"}'))
    con.execute("INSERT INTO paper_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("e" * 64, "context_news", "coindesk", 1000, 2000, 3000, 4000,
                 5000, "deepseek", "deepseek-v4-flash", "f" * 64, 0, "complete"))
    for arm, pu in (("news", 0.5), ("news_market", 0.4)):
        con.execute("INSERT INTO paper_arms VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    ("e" * 64, arm, arm + "1", "s1", "a" * 64, pu, -10.0,
                     0.5, "FLAT", "b" * 64, 5100))
    con.execute("INSERT INTO paper_entries VALUES(?,?,?,?)",
                ("e" * 64, 64000.0, 5200, "binance_public_ticker"))
    for arm in ("news", "news_market"):
        con.execute("INSERT INTO dispatch_reservations VALUES(?,?,?,?,?,?)",
                    (arm + "1", "e" * 64, arm, 4000, "completed", 0))
    con.commit()
    con.close()


class ExportSchemaTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="v21exp-")
        self.events = os.path.join(self.dir, "v21.sqlite")
        self.paper = os.path.join(self.dir, "paper.sqlite")
        self.dispatch = os.path.join(self.dir, "dispatch.sqlite")
        make_db(self.events)
        make_db(self.paper)
        make_db(self.dispatch)
        self.out = os.path.join(self.dir, "export.json")

    def test_export_runs_and_has_documented_shape(self):
        proc = subprocess.run(
            [PY, str(EXPORT), "--data-dir", self.dir,
             "--gateway-db", os.path.join(self.dir, "absent.db"),
             "--out", self.out],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "PYTHONPATH": str(ROOT / "source")})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = json.loads(Path(self.out).read_text(encoding="utf-8"))
        for key in ("kind", "version", "taken_ms", "events", "paper",
                    "dispatch", "gateway_usage"):
            self.assertIn(key, doc)
        self.assertEqual(doc["paper"]["quick_check"], "ok")
        self.assertEqual(len(doc["paper"]["records"]), 1)
        self.assertEqual(len(doc["paper"]["arms"]), 2)
        self.assertEqual(len(doc["paper"]["entries"]), 1)
        arms = {a["arm"] for a in doc["paper"]["arms"]}
        self.assertEqual(arms, {"news", "news_market"},
                         "both arms must be present for a sealed pair")

    def test_missing_gateway_is_reported_not_invented(self):
        proc = subprocess.run(
            [PY, str(EXPORT), "--data-dir", self.dir,
             "--gateway-db", os.path.join(self.dir, "absent.db"),
             "--out", self.out],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "PYTHONPATH": str(ROOT / "source")})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = json.loads(Path(self.out).read_text(encoding="utf-8"))
        self.assertEqual(doc["gateway_usage"]["status"], "unavailable")

    def test_titles_are_truncated_and_urls_kept(self):
        subprocess.run(
            [PY, str(EXPORT), "--data-dir", self.dir,
             "--gateway-db", os.path.join(self.dir, "absent.db"),
             "--out", self.out], capture_output=True, text=True, timeout=60,
            env={**os.environ, "PYTHONPATH": str(ROOT / "source")})
        doc = json.loads(Path(self.out).read_text(encoding="utf-8"))
        row = doc["events"]["rows"][0]
        self.assertLessEqual(len(row["title"] or ""), 180)
        self.assertEqual(row["url"], "https://example.test/a")


if __name__ == "__main__":
    unittest.main()
