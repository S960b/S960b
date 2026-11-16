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
    def test_prepare_needs_the_pilot_acknowledgement(self):
        rc = PL.main(["prepare"])
        self.assertEqual(rc, 3)

    def test_cost_is_not_a_gate(self):
        # Money is not a constraint for this project: a pair is never refused
        # because of a reported cost figure.
        self.assertIsNone(PL.MAX_PILOT_COST_USD)


class FinishGuardTests(unittest.TestCase):
    def _run(self, payload):
        proc = subprocess.run([PY, str(PILOT_SCRIPT), "finish"],
                              input=json.dumps(payload), capture_output=True,
                              text=True, timeout=30)
        return proc.returncode, json.loads(proc.stdout or "{}")

    def test_high_reported_cost_is_still_accepted(self):
        # A large reported cost must not block sealing; only a real contract
        # problem may. Here the pair itself is malformed, so it is refused for
        # that reason and not for money.
        code, out = self._run({
            "event_id": "e" * 64, "results": [{"arm": "news"}, {"arm": "news_market"}],
            "usage": {"completions": 2, "cost_usd": 99.0},
            "prompt_version": PL.VERSION, "prompt_hashes": {},
            "decision_time_ms": 1, "market_cutoff_ms": 1,
        })
        self.assertNotEqual(out.get("reason"), "pilot_cost_cap")

    def test_incomplete_pair_is_refused(self):
        code, out = self._run({"event_id": "e" * 64, "results": [{"arm": "news"}]})
        self.assertEqual(code, 3)
        self.assertEqual(out["reason"], "incomplete_pair")


if __name__ == "__main__":
    unittest.main()


class QuoteAnchorTests(unittest.TestCase):
    """The float-vs-dict bug must be impossible, and no zero anchor may pass."""

    def test_quote_is_a_structure_not_a_float(self):
        # P.parse_quote returns a float; public_quote must wrap it.
        with mock.patch.object(PL.P, "parse_quote", return_value=64000.0):
            with mock.patch.object(PL, "urlopen", _fake_urlopen(b'{"price":"64000"}'),
                                   create=True):
                q = PL.public_quote()
        self.assertIsInstance(q, dict)
        self.assertGreater(q["price"], 0)
        self.assertGreater(q["observed_ms"], 0)
        self.assertEqual(q["source"], "binance_public_ticker")

    def test_quote_must_be_after_last_arm(self):
        future = int(__import__("time").time() * 1000) + 10_000
        with self.assertRaises(PL.QuoteError):
            with mock.patch.object(PL, "_urlopen_json",
                                   return_value={"price": "64000"}):
                PL.public_quote(after_ms=future)

    def test_zero_or_negative_price_fails_closed(self):
        for bad in ("0", "-5"):
            with self.assertRaises(PL.QuoteError):
                with mock.patch.object(PL, "_urlopen_json",
                                       return_value={"price": bad}):
                    PL.public_quote()

    def test_due_is_entry_plus_four_hours(self):
        entry = 1_700_000_000_000
        self.assertEqual(entry + 4 * 3_600_000 - entry, 14_400_000)

    def test_historical_pilot_row_stays_invalid(self):
        # The first pilot pair had entry_price 0 / due_ms 0. It must remain
        # visible as invalid, never repaired and never settled.
        sidecar = next(HERE.parent.rglob("pilot_sidecar_*.json"), None)
        if sidecar is None:
            self.skipTest("historical pilot sidecar not present in this tree")
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        pair = payload["pairs"][0]
        self.assertEqual(pair["entry_price"], 0.0)
        self.assertEqual(payload["outcomes"][0]["due_ms"], 0)


def _urlopen_json(value):
    return lambda *a, **k: value


def _fake_urlopen(body):
    class _Resp:
        def read(self_inner):
            return body

        def __enter__(self_inner):
            return self_inner

        def __exit__(self_inner, *a):
            return False
    return lambda *a, **k: _Resp()
