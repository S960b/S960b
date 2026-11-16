"""NEWS v2.1 — the one-off PROMPT_ONLY pilot lane.

The pilot is deliberately separate from production: its own ledger, its own
labels, its own cost cap, and a hard refusal to be sealed as compliant when any
tool call appears.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _cand in (ROOT / 'source', ROOT / 'scripts', HERE):
    if (_cand / 'v21_pilot_cycle.py').is_file():
        sys.path.insert(0, str(_cand))
        PILOT_SCRIPT = _cand / 'v21_pilot_cycle.py'
        break
else:  # pragma: no cover
    raise ImportError('v21_pilot_cycle.py not published')

import v21_pilot_cycle as PL

PY = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable


class PilotLedgerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="v21pilot-")
        self.db = os.path.join(self.dir, "pilot.sqlite")
        self._orig = PL.PILOT_DB
        PL.PILOT_DB = Path(self.db)

    def tearDown(self):
        PL.PILOT_DB = self._orig
        for suf in ("", "-wal", "-shm"):
            try:
                os.unlink(self.db + suf)
            except FileNotFoundError:
                pass

    def test_ledger_is_separate_from_production(self):
        con = PL.open_pilot()
        try:
            tables = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()
        self.assertEqual(tables,
                         {"pilot_pairs", "pilot_arms", "pilot_outcomes"})
        self.assertNotIn("paper_records", tables,
                         "a pilot must never write the production journal")

    def test_schema_is_idempotent(self):
        for _ in range(3):
            con = PL.open_pilot()
            con.close()
        con = PL.open_pilot()
        try:
            cols = [r[1] for r in con.execute("PRAGMA table_info(pilot_pairs)")]
            self.assertEqual(cols.count("event_id"), 1)
        finally:
            con.close()


class AcknowledgementGateTests(unittest.TestCase):
    def test_prepare_needs_both_acknowledgements(self):
        rc = PL.main(["prepare"])
        self.assertEqual(rc, 3)
        rc = PL.main(["prepare", "--acknowledge-pilot"])
        self.assertEqual(rc, 3)


class FinishGuardTests(unittest.TestCase):
    def _run(self, payload):
        proc = subprocess.run([PY, str(PILOT_SCRIPT), "finish"],
                              input=json.dumps(payload), capture_output=True,
                              text=True, timeout=30)
        return proc.returncode, json.loads(proc.stdout or "{}")

    def test_cost_cap_refuses_before_writing(self):
        code, out = self._run({
            "event_id": "e" * 64, "results": [{"arm": "news"}, {"arm": "news_market"}],
            "usage": {"completions": 2, "cost_usd": 9.0},
            "prompt_version": PL.VERSION, "prompt_hashes": {},
            "decision_time_ms": 1, "market_cutoff_ms": 1,
        })
        self.assertEqual(code, 3)
        self.assertEqual(out["reason"], "pilot_cost_cap")

    def test_incomplete_pair_is_refused(self):
        code, out = self._run({"event_id": "e" * 64, "results": [{"arm": "news"}]})
        self.assertEqual(code, 3)
        self.assertEqual(out["reason"], "incomplete_pair")


if __name__ == "__main__":
    unittest.main()
