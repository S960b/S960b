"""NEWS v2.1 stage 2B — adversarial regressions for D12, D15, D16, D17, D18.

Each test states the pre-fix behaviour it replaces. No network, no model call,
no live database and no GUI dependency.
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent if (HERE.parent / 'source').is_dir() or \
    (HERE.parent / 'v21_dispatch.py').is_file() else HERE
for _cand in (ROOT / 'source', ROOT, ROOT / 'scripts', HERE):
    if (_cand / 'v21_dispatch.py').is_file():
        sys.path.insert(0, str(_cand))
        break

import v21_dispatch as D
import v21_paper_prep as P
import v21_pilot_cycle as C

M = 60_000
BASE = 1_800_000_000_000
GOOD = ('{"p_up":0.62,"expected_return_bps":70,'
        '"uncertainty":0.45,"rationale":"hypothesis"}')
MODEL = {"providerID": "deepseek", "modelID": "deepseek-v4-flash"}


class StrictJsonTests(unittest.TestCase):
    """D17: `true` parsed as 1.0 and a duplicated key silently took the last
    value, so a decision the model never wrote could pass validation."""

    def test_bool_is_rejected_where_a_number_is_required(self):
        for text in (
            '{"p_up":true,"expected_return_bps":70,"uncertainty":0.4,"rationale":"x"}',
            '{"p_up":0.6,"expected_return_bps":true,"uncertainty":0.4,"rationale":"x"}',
            '{"p_up":0.6,"expected_return_bps":70,"uncertainty":false,"rationale":"x"}',
        ):
            with self.assertRaises(P.PrepError):
                P.parse_arm_response(text)

    def test_duplicate_key_is_rejected(self):
        text = ('{"p_up":0.2,"expected_return_bps":70,"uncertainty":0.4,'
                '"rationale":"x","p_up":0.9}')
        with self.assertRaises(P.PrepError):
            P.parse_arm_response(text)

    def test_non_standard_constants_are_rejected(self):
        for bad in ("NaN", "Infinity", "-Infinity"):
            text = ('{"p_up":%s,"expected_return_bps":70,"uncertainty":0.4,'
                    '"rationale":"x"}' % bad)
            with self.assertRaises(P.PrepError):
                P.parse_arm_response(text)

    def test_malformed_json_is_rejected(self):
        for bad in ("", "{}", '{"p_up":0.6', "[]", "null"):
            with self.assertRaises(P.PrepError):
                P.parse_arm_response(bad)

    def test_valid_response_still_parses(self):
        self.assertEqual(P.parse_arm_response(GOOD)["p_up"], 0.62)

    def test_integers_are_still_numbers(self):
        ok = ('{"p_up":1,"expected_return_bps":70,"uncertainty":0,'
              '"rationale":"x"}')
        self.assertEqual(P.parse_arm_response(ok)["p_up"], 1.0)


class WriterContractTests(unittest.TestCase):
    """D12: the writer sealed whatever the caller handed over."""

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(P.SCHEMA_RECORDS)
        P.migrate_records(self.con)
        self.quote = {"price": 74000.0, "observed_ms": BASE + 220_000}

    def arms(self, first=None, second=None):
        a = {"arm": "news", "attempt_id": "a1", "session_id": "s1",
             "text": GOOD, "model": dict(MODEL),
             "session_terminal": "completed", "completed_ms": BASE + 100_000}
        b = {"arm": "news_market", "attempt_id": "a2", "session_id": "s2",
             "text": GOOD, "model": dict(MODEL),
             "session_terminal": "completed", "completed_ms": BASE + 200_000}
        a.update(first or {})
        b.update(second or {})
        return [a, b]

    def test_valid_contract_passes(self):
        P.validate_arm_contract(self.arms(), self.quote)

    def test_wrong_arm_names_refused(self):
        arms = self.arms()
        arms[1]["arm"] = "market"
        with self.assertRaises(P.PrepError) as ctx:
            P.validate_arm_contract(arms, self.quote)
        self.assertIn("unexpected_arm_names", str(ctx.exception))

    def test_duplicate_arm_names_refused(self):
        arms = self.arms()
        arms[1]["arm"] = "news"
        with self.assertRaises(P.PrepError) as ctx:
            P.validate_arm_contract(arms, self.quote)
        self.assertIn("duplicate_arm_names", str(ctx.exception))

    def test_empty_model_identity_refused(self):
        for bad in ({}, {"modelID": "x"},
                    {"providerID": "deepseek", "modelID": "  "}):
            with self.assertRaises(P.PrepError):
                P.validate_arm_contract(self.arms(first={"model": bad}),
                                        self.quote)

    def test_empty_session_id_refused(self):
        for bad in ("", None, "   "):
            with self.assertRaises(P.PrepError):
                P.validate_arm_contract(self.arms(first={"session_id": bad}),
                                        self.quote)

    def test_non_terminal_state_refused(self):
        for state in ("running", "pending", None, "error"):
            with self.assertRaises(P.PrepError) as ctx:
                P.validate_arm_contract(
                    self.arms(first={"session_terminal": state}), self.quote)
            self.assertIn("arm_not_terminal", str(ctx.exception))

    def test_entry_must_be_after_last_arm(self):
        arms = self.arms()
        last = max(a["completed_ms"] for a in arms)
        with self.assertRaises(P.PrepError) as ctx:
            P.validate_arm_contract(
                arms, {"price": 74000.0, "observed_ms": last})
        self.assertIn("entry_not_after_last_arm_completion", str(ctx.exception))

    def test_bool_price_refused(self):
        with self.assertRaises(P.PrepError):
            P.validate_arm_contract(
                self.arms(), {"price": True, "observed_ms": BASE + 220_000})


class CandleEvidenceTests(unittest.TestCase):
    """D15: the settlement accepted any candle whose close was near due."""

    def setUp(self):
        self.due = BASE + 30_000            # inside the first minute
        self.now = BASE + M * 2

    def candle(self, span=59_999, price=64000.0, open_ms=BASE):
        return [(open_ms, open_ms + span, price)]

    def test_one_complete_minute_is_accepted(self):
        out = C.pick_exit_candle(self.candle(), self.due, self.now)
        self.assertEqual(out["span_ms"], 59_999)
        self.assertEqual(out["symbol"], "BTCUSDT")
        self.assertGreaterEqual(out["drift_ms"], 0)
        self.assertLess(out["drift_ms"], 60_000)

    def test_partially_formed_candle_refused(self):
        with self.assertRaises(P.PrepError) as ctx:
            C.pick_exit_candle(self.candle(span=0), self.due, self.now)
        self.assertIn("candle_not_one_complete_minute", str(ctx.exception))

    def test_sixty_second_span_refused(self):
        with self.assertRaises(P.PrepError):
            C.pick_exit_candle(self.candle(span=60_000), self.due, self.now)

    def test_unaligned_open_refused(self):
        with self.assertRaises(P.PrepError) as ctx:
            C.pick_exit_candle(self.candle(open_ms=BASE + 1), self.due, self.now)
        self.assertIn("candle_open_not_aligned", str(ctx.exception))

    def test_nan_and_non_float_price_refused(self):
        with self.assertRaises(P.PrepError):
            C.pick_exit_candle(self.candle(price=float("nan")), self.due, self.now)
        with self.assertRaises(P.PrepError):
            C.pick_exit_candle(self.candle(price=64000), self.due, self.now)

    def test_pending_candle_is_reported_not_used(self):
        with self.assertRaises(P.PrepError) as ctx:
            C.pick_exit_candle(self.candle(), self.due, BASE + 10_000)
        self.assertIn("label_pending_candle_open", str(ctx.exception))

    def test_drift_bound_is_inclusive_only_below_one_minute(self):
        # The only way drift can equal 60000 for a real 1m candle is an
        # unaligned open, which is refused earlier as bad evidence. This pins
        # the bound itself: [0, 60000) and nothing wider.
        self.assertEqual(C.PILOT_DRIFT_BOUND_MS, 60_000)
        # due inside the candle, so close - due is a real drift value
        ok = C.pick_exit_candle(self.candle(), self.due, self.now)
        self.assertLess(ok["drift_ms"], C.PILOT_DRIFT_BOUND_MS)
        self.assertGreaterEqual(ok["drift_ms"], 0)

    def test_earlier_candle_is_never_substituted(self):
        candles = [(BASE - M, BASE - M + 59_999, 64000.0)]
        with self.assertRaises(P.PrepError):
            C.pick_exit_candle(candles, self.due, self.now)

    def test_empty_candles_refused(self):
        with self.assertRaises(P.PrepError):
            C.pick_exit_candle([], self.due, self.now)


class LeaseIsolationTests(unittest.TestCase):
    """D16: one singleton lease row let a synthetic run delete the production
    lease, and the two lanes could block each other."""

    def setUp(self):
        self.con = D.open_db(":memory:", create=True)
        self.con.executescript(D.SCHEMA_DISPATCH
                               if hasattr(D, "SCHEMA_DISPATCH") else "")

    def _lease(self, bucket, holder, expires):
        self.con.execute(
            "INSERT INTO dispatch_lease(id,holder,acquired_ms,expires_ms,"
            "synthetic) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "holder=excluded.holder, expires_ms=excluded.expires_ms",
            (D.lease_row(bucket), holder, BASE, expires, bucket))
        self.con.commit()

    def test_rows_are_distinct(self):
        self.assertEqual(D.lease_row(0), 0)
        self.assertEqual(D.lease_row(1), 1)

    def test_synthetic_does_not_overwrite_production(self):
        self._lease(0, "prod-event", BASE + M)
        self._lease(1, "synth-event", BASE + M)
        rows = dict(self.con.execute(
            "SELECT id,holder FROM dispatch_lease").fetchall())
        self.assertEqual(rows, {0: "prod-event", 1: "synth-event"})

    def test_schema_refuses_foreign_ids(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute(
                "INSERT INTO dispatch_lease(id,holder,acquired_ms,expires_ms,"
                "synthetic) VALUES(7,'x',1,2,0)")


class ReconciliationStatusTests(unittest.TestCase):
    """D18: every attempt without a record was reported as "refused"."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        import v21_export as E
        self.E = E

    def test_live_attempt_is_not_reported_as_refused(self):
        self.assertEqual(self.E.dispatch_status({"reserved": 2}), "reserved")
        self.assertEqual(self.E.dispatch_status({"running": 1}), "running")

    def test_live_outranks_failed(self):
        self.assertEqual(
            self.E.dispatch_status({"reserved": 1, "failed": 1}), "reserved")

    def test_unknown_state_is_unknown(self):
        self.assertEqual(self.E.dispatch_status({"weird": 1}), "unknown")

    def test_failed_and_completed_are_explicit(self):
        self.assertEqual(self.E.dispatch_status({"failed": 2}), "failed")
        self.assertEqual(self.E.dispatch_status({"completed": 2}), "completed")


class CohortMigrationTests(unittest.TestCase):
    """Stage-2A cohort columns must be additive on a real-shaped ledger."""

    def test_migration_on_pre_column_schema_keeps_rows(self):
        path = tempfile.mktemp(suffix=".sqlite")
        con = sqlite3.connect(path)
        con.executescript("""
          CREATE TABLE paper_records(event_id TEXT PRIMARY KEY,
            cohort TEXT NOT NULL, source TEXT NOT NULL, published_ms INTEGER,
            observed_ms INTEGER, cutoff_ms INTEGER NOT NULL,
            reserved_ms INTEGER NOT NULL, completed_ms INTEGER,
            provider_id TEXT, model_id TEXT, seal_sha256 TEXT NOT NULL,
            synthetic INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL);""")
        con.execute("INSERT INTO paper_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("e" * 64, "context_news", "coindesk", 1, 2, 3, 4, 5,
                     "deepseek", "deepseek-v4-flash", "f" * 64, 0, "complete"))
        con.commit()
        con.close()

        import importlib
        P2 = importlib.reload(P)
        rec = P2.open_records(path)
        try:
            cols = [r[1] for r in rec.execute("PRAGMA table_info(paper_records)")]
            for name in ("declared_cohort", "effective_cohort",
                         "cohort_provenance"):
                self.assertIn(name, cols)
            row = rec.execute("SELECT event_id,cohort,declared_cohort,"
                              "effective_cohort FROM paper_records").fetchone()
            self.assertEqual(row[0], "e" * 64)
            self.assertEqual(row[1], "context_news",
                             "the stored cohort must not be rewritten")
            self.assertIsNone(row[2], "no invented declared cohort")
            self.assertIsNone(row[3], "no invented effective cohort")
        finally:
            rec.close()
            for suf in ("", "-wal", "-shm"):
                try:
                    os.unlink(path + suf)
                except FileNotFoundError:
                    pass


if __name__ == "__main__":
    unittest.main()
