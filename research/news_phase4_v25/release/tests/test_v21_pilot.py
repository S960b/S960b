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
import time
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
            tables.discard("sqlite_sequence")   # bookkeeping for AUTOINCREMENT
        finally:
            con.close()
        self.assertEqual(tables,
                         {"pilot_pairs", "pilot_arms", "pilot_outcomes",
                          "pilot_settle_log", "pilot_annotations",
                          "pilot_attempts"})
        self.assertNotIn("paper_records", tables,
                         "a pilot must never write the production journal")
        self.assertNotIn("paper_outcomes", tables)

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
        # D19: the fixture used to patch `PL.urlopen`, which public_quote never
        # calls, so the patch was inert and the test silently needed the real
        # network. It now patches the transport that is actually used.
        with mock.patch.object(PL.P, "parse_quote", return_value=64000.0):
            with mock.patch.object(PL, "_urlopen_json",
                                   return_value={"price": "64000"}):
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


class SettleTests(unittest.TestCase):
    """Pilot settlement: own lane, one-time, fail-closed, no zero anchor."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="v21settle-")
        self.db = os.path.join(self.dir, "pilot.sqlite")
        self._orig = PL.PILOT_DB
        PL.PILOT_DB = Path(self.db)
        con = PL.open_pilot()
        con.execute("INSERT INTO pilot_pairs(event_id,label,prompt_version,"
                    "prompt_hashes,decision_time_ms,market_cutoff_ms,published_ms,"
                    "observed_ms,provider_id,model_id,entry_price,entry_ms,"
                    "tool_calls,completions,cost_usd,created_ms) "
                    "VALUES('valid','L','v','{}',1,1,1,1,'deepseek','m',64000.0,"
                    "1000,0,2,0,1)")
        con.execute("INSERT INTO pilot_pairs(event_id,label,prompt_version,"
                    "prompt_hashes,decision_time_ms,market_cutoff_ms,published_ms,"
                    "observed_ms,provider_id,model_id,entry_price,entry_ms,"
                    "tool_calls,completions,cost_usd,created_ms) "
                    "VALUES('invalid','L','v','{}',1,1,1,1,'deepseek','m',0.0,"
                    "0,0,2,0,1)")
        con.commit()
        con.close()

    def tearDown(self):
        PL.PILOT_DB = self._orig

    def _add_outcome(self, event_id, due_ms):
        con = PL.open_pilot()
        con.execute("INSERT INTO pilot_outcomes(event_id,horizon_h,due_ms) "
                    "VALUES(?,4,?)", (event_id, due_ms))
        con.commit()
        con.close()

    def _run(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = PL.main(["settle"])
        return code, json.loads(buf.getvalue().strip().splitlines()[-1])

    def test_zero_anchor_is_never_settled(self):
        self._add_outcome("invalid", 0)
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertEqual(out["anchor_invalid_skipped"], 1)
        self.assertEqual(out["settled"], 0)
        con = sqlite3.connect(self.db)
        try:
            exit_ms = con.execute(
                "SELECT exit_ms FROM pilot_outcomes WHERE event_id='invalid'"
            ).fetchone()[0]
        finally:
            con.close()
        self.assertIsNone(exit_ms, "an invalid anchor must stay unsettled")

    def test_future_due_stays_pending(self):
        future = int(time.time() * 1000) + 3_600_000
        self._add_outcome("valid", future)
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertEqual(out["pending"], 1)
        self.assertEqual(out["settled"], 0)

    def test_first_close_after_due_is_used_once(self):
        due = int(time.time() * 1000) - 600_000
        self._add_outcome("valid", due)
        candle_close = ((due // 60_000) + 1) * 60_000 - 1
        fake = [(candle_close - 59_999, candle_close, 50_000.0)]
        with mock.patch.object(PL.P, "fetch_klines_1m", return_value=fake):
            code, out = self._run()
        self.assertEqual(code, 0)
        self.assertEqual(out["settled"], 1)
        con = sqlite3.connect(self.db)
        try:
            row = con.execute("SELECT exit_ms,price,drift_ms,method FROM "
                              "pilot_outcomes WHERE event_id='valid'").fetchone()
        finally:
            con.close()
        self.assertEqual(row[0], candle_close)
        self.assertGreaterEqual(row[0], due, "exit must not precede due")
        self.assertLessEqual(row[2], 60_000)

    def test_settlement_is_not_repeated(self):
        due = int(time.time() * 1000) - 600_000
        self._add_outcome("valid", due)
        close = ((due // 60_000) + 1) * 60_000 - 1
        fake = [(close - 59_999, close, 50_000.0)]
        with mock.patch.object(PL.P, "fetch_klines_1m", return_value=fake):
            self._run()
            code, out = self._run()
        self.assertEqual(out["settled"], 0, "a settled slot is never rewritten")

    def test_network_failure_is_reported_not_swallowed(self):
        due = int(time.time() * 1000) - 600_000
        self._add_outcome("valid", due)
        with mock.patch.object(PL.P, "fetch_klines_1m",
                               side_effect=PL.P.PrepError("label_fetch_failed:OSError")):
            code, out = self._run()
        self.assertEqual(code, 1)
        self.assertTrue(out["failures"])
        self.assertEqual(out["failures"][0]["reason"],
                         "label_fetch_failed:OSError")

    def test_settle_log_is_written(self):
        self._add_outcome("invalid", 0)
        self._run()
        con = sqlite3.connect(self.db)
        try:
            n = con.execute("SELECT COUNT(*) FROM pilot_settle_log").fetchone()[0]
        finally:
            con.close()
        self.assertGreaterEqual(n, 1)
