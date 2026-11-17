"""Stage-28 label watcher: bounded, dry by default, idempotent, no model.

Every test runs on a disposable SQLite fixture. Nothing here touches the live
database, the collector, or any activation flag.
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

import tier2 as T2
import tier2_label_watch as LW
import tier2_trigger as TR

NOW = 1_800_000_000_000


def candle_for(due_ms, price=64_000.0):
    open_ms = (due_ms // 60_000) * 60_000
    if open_ms < due_ms:
        open_ms += 60_000
    while open_ms + 59_999 - due_ms >= 60_000:
        open_ms -= 60_000
    return {"symbol": "BTCUSDT", "closed": True, "open_ms": open_ms,
            "close_ms": open_ms + 59_999, "close": price,
            "source": "binance_public_1m_kline"}


class LabelWatchTests(unittest.TestCase):

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="lw-"))
        self.db = self.dir / "tier2.sqlite"
        con = T2.open_tier2(self.db)
        T2.open_attempts(con)
        self.con = con

    def tearDown(self):
        try:
            self.con.close()
        except Exception:
            pass

    def _seal(self, event_id="a" * 64, cohort="geopolitics"):
        ev = {"event_id": event_id, "cohort": cohort,
              "published_ms": NOW - 120_000, "observed_ms": NOW - 60_000}
        out = T2.mock_triple(con=self.con, event=ev, now_ms=NOW,
                             attestation_path=self.dir / "att.jsonl")
        return out["due_ms"]

    def _settle(self, due, *, now_ms, dry=False, candle=None, **kw):
        return LW.settle(db_path=self.db, now_ms=now_ms,
                         candle_fn=candle or (lambda *, due_ms: candle_for(due_ms)),
                         dry=dry, **kw)

    # --- basic states ---------------------------------------------------

    def test_no_triples(self):
        out = LW.settle(db_path=self.db, now_ms=NOW,
                        candle_fn=lambda *, due_ms: candle_for(due_ms))
        self.assertEqual(out["status"], "no_triples")

    def test_not_due_writes_nothing(self):
        due = self._seal()
        out = self._settle(due, now_ms=due - 60_000)
        self.assertEqual(out["outcomes"][0]["status"], "pending")
        self.assertEqual(out["outcomes"][0]["reason"], "not_due_yet")
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM tier2_outcomes").fetchone()[0], 0)

    def test_closed_candle_labels_once(self):
        due = self._seal()
        out = self._settle(due, now_ms=due + 120_000)
        self.assertEqual(out["outcomes"][0]["status"], "labelled")
        rows = self.con.execute(
            "SELECT label_state, price, source FROM tier2_outcomes").fetchall()
        self.assertEqual(rows, [("labelled", 64_000.0, "binance_public_1m_kline")])

    def test_second_pass_is_idempotent(self):
        due = self._seal()
        self._settle(due, now_ms=due + 120_000)
        again = self._settle(due, now_ms=due + 300_000)
        self.assertTrue(again["outcomes"][0]["already"])
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM tier2_outcomes").fetchone()[0], 1)

    def test_dry_mode_writes_nothing_at_all(self):
        due = self._seal()
        before = self.con.execute(
            "SELECT COUNT(*) FROM tier2_outcomes").fetchone()[0]
        out = self._settle(due, now_ms=due + 120_000, dry=True)
        self.assertEqual(out["outcomes"][0]["status"], "would_label")
        self.assertFalse(out["dry"] is False)
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM tier2_outcomes").fetchone()[0], before)
        tables = {r[0] for r in self.con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("tier2_label_attempts", tables,
                         "dry mode must not even create the attempts table")

    # --- failures -------------------------------------------------------

    def test_delayed_quote_stays_pending_then_recovers(self):
        due = self._seal()
        calls = {"n": 0}

        def flaky(*, due_ms):
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("binance slow")
            return candle_for(due_ms)

        for i in range(2):
            out = self._settle(due, now_ms=due + 120_000, candle=flaky)
            self.assertEqual(out["outcomes"][0]["status"], "pending")
            self.assertEqual(out["outcomes"][0]["attempts"], i + 1)
        out = self._settle(due, now_ms=due + 180_000, candle=flaky)
        self.assertEqual(out["outcomes"][0]["status"], "labelled")

    def test_bounded_censor_after_max_failures(self):
        due = self._seal()

        def outage(*, due_ms):
            raise ConnectionError("down")

        for _ in range(LW.MAX_FAILURES - 1):
            out = self._settle(due, now_ms=due + 60_000, candle=outage)
            self.assertEqual(out["outcomes"][0]["status"], "pending")
        out = self._settle(due, now_ms=due + 60_000, candle=outage)
        self.assertEqual(out["outcomes"][0]["status"], "censored")
        price = self.con.execute(
            "SELECT price FROM tier2_outcomes").fetchone()[0]
        self.assertEqual(price, 0.0)

    def test_censor_also_fires_on_the_deadline(self):
        due = self._seal()

        def outage(*, due_ms):
            raise ConnectionError("down")

        out = self._settle(due, now_ms=due + 120_000, candle=outage,
                           max_wait_ms=60_000)
        self.assertEqual(out["outcomes"][0]["status"], "censored")

    def test_censored_row_is_terminal(self):
        due = self._seal()

        def outage(*, due_ms):
            raise ConnectionError("down")

        for _ in range(LW.MAX_FAILURES):
            self._settle(due, now_ms=due + 60_000, candle=outage)
        again = self._settle(due, now_ms=due + 600_000)
        self.assertEqual(again["outcomes"][0]["status"], "censored")

    def test_invalid_candle_does_not_write_a_price(self):
        due = self._seal()
        bad = lambda *, due_ms: {"symbol": "BTCUSDT", "closed": False,
                                 "open_ms": due, "close_ms": due + 59_999,
                                 "close": 1.0}
        out = self._settle(due, now_ms=due + 60_000, candle=bad)
        self.assertEqual(out["outcomes"][0]["status"], "pending")
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM tier2_outcomes").fetchone()[0], 0)

    def test_wrong_symbol_is_refused(self):
        due = self._seal()
        bad = lambda *, due_ms: dict(candle_for(due_ms), symbol="ETHUSDT")
        out = self._settle(due, now_ms=due + 60_000, candle=bad)
        self.assertEqual(out["outcomes"][0]["status"], "pending")

    def test_invalid_sealed_entry_is_refused(self):
        self._seal()
        self.con.execute("UPDATE tier2_triples SET entry_observed_ms=0")
        self.con.commit()
        with self.assertRaises(LW.LabelWatchError):
            LW.sealed_rows(self.con)

    def test_missing_db_is_refused(self):
        with self.assertRaises(LW.LabelWatchError):
            LW.settle(db_path=self.dir / "absent.sqlite", now_ms=NOW,
                      candle_fn=lambda *, due_ms: candle_for(due_ms))

    # --- restart / concurrency -----------------------------------------

    def test_restart_resumes_from_attempt_counter(self):
        due = self._seal()

        def outage(*, due_ms):
            raise ConnectionError("down")

        self._settle(due, now_ms=due + 60_000, candle=outage)
        attempts = self.con.execute(
            "SELECT attempts FROM tier2_label_attempts").fetchone()[0]
        self.con.close()
        self.con = T2.open_tier2(self.db)          # simulate a fresh process
        out = self._settle(due, now_ms=due + 60_000, candle=outage)
        self.assertEqual(out["outcomes"][0]["attempts"], attempts + 1)

    def test_concurrent_processes_write_one_outcome(self):
        import subprocess
        due = self._seal()
        script = f"""
import sys
sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})
import tier2_label_watch as LW
due = {due}
def candle(*, due_ms):
    o = (due_ms // 60000) * 60000
    if o < due_ms: o += 60000
    while o + 59999 - due_ms >= 60000: o -= 60000
    return {{"symbol": "BTCUSDT", "closed": True, "open_ms": o,
             "close_ms": o + 59999, "close": 64000.0,
             "source": "binance_public_1m_kline"}}
out = LW.settle(db_path={str(self.db)!r}, now_ms=due + 120000,
                candle_fn=candle, dry=False)
print(out["outcomes"][0]["status"])
"""
        procs = [subprocess.Popen(["/usr/bin/python3", "-c", script],
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
                 for _ in range(4)]
        outs = []
        for p in procs:
            o, e = p.communicate(timeout=25)
            outs.append(o.strip() or ("ERR:" + e.strip()[-100:]))
        count = self.con.execute(
            "SELECT COUNT(*) FROM tier2_outcomes").fetchone()[0]
        self.assertEqual(count, 1, outs)


class Stage29Tests(unittest.TestCase):
    """The four defects stage 29 named: synthesized candle, racy attempts,
    bugs classified as outages, censored leaking into metrics."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="s29-"))
        self.db = self.dir / "tier2.sqlite"
        self.con = T2.open_tier2(self.db)
        T2.open_attempts(self.con)

    def tearDown(self):
        try:
            self.con.close()
        except Exception:
            pass

    def _seal(self):
        ev = {"event_id": "b" * 64, "cohort": "geopolitics",
              "published_ms": NOW - 120_000, "observed_ms": NOW - 60_000}
        return T2.mock_triple(con=self.con, event=ev, now_ms=NOW,
                              attestation_path=self.dir / "att.jsonl")["due_ms"]

    # --- 1. no invented candle fields ----------------------------------

    def test_exchange_candle_uses_payload_timestamps(self):
        due = 1_800_000_000_000
        rows = [(1_799_999_940_000, 1_799_999_999_999, 64_000.0),
                (1_800_000_000_000, 1_800_000_059_999, 64_100.0)]
        import v21_paper_prep as P
        saved = P.fetch_klines_1m
        P.fetch_klines_1m = lambda *a, **k: rows
        try:
            c = LW.exchange_candle(due_ms=due, now_ms=due + 120_000)
        finally:
            P.fetch_klines_1m = saved
        self.assertEqual(c["open_ms"], 1_800_000_000_000)
        self.assertEqual(c["close_ms"], 1_800_000_059_999)
        self.assertTrue(c["closed"])
        self.assertEqual(c["source"], "binance_public_1m_kline")

    def test_exchange_candle_refuses_when_minute_is_still_open(self):
        due = 1_800_000_000_000
        rows = [(1_800_000_000_000, 1_800_000_059_999, 64_000.0)]
        import v21_paper_prep as P
        saved = P.fetch_klines_1m
        P.fetch_klines_1m = lambda *a, **k: rows
        try:
            c = LW.exchange_candle(due_ms=due, now_ms=due + 10_000)
        finally:
            P.fetch_klines_1m = saved
        self.assertFalse(c["closed"], "closure comes from the payload, not a guess")

    # --- 3. a bug must not censor --------------------------------------

    def test_classify_separates_bug_from_outage(self):
        self.assertEqual(LW.classify(ConnectionError("down")), "transient")
        self.assertEqual(LW.classify(RuntimeError("label_fetch_failed:x")),
                         "transient")
        self.assertEqual(LW.classify(TypeError("bad")), "bug")
        self.assertEqual(LW.classify(KeyError("close")), "bug")

    def test_programming_error_does_not_write_censored(self):
        due = self._seal()

        def buggy(*, due_ms):
            raise TypeError("cannot unpack non-sequence")

        out = LW.settle(db_path=self.db, now_ms=due + 120_000,
                        candle_fn=buggy, dry=False)
        self.assertEqual(out["outcomes"][0]["status"], "error")
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM tier2_outcomes").fetchone()[0], 0)
        self.assertEqual(LW._attempts_of(self.con, "b" * 64)[0], 0)

    # --- 2. atomic attempts --------------------------------------------

    def test_failure_after_success_is_not_counted(self):
        due = self._seal()
        LW.settle(db_path=self.db, now_ms=due + 120_000,
                  candle_fn=lambda *, due_ms: candle_for(due_ms), dry=False)

        def outage(*, due_ms):
            raise ConnectionError("too late")

        out = LW.settle(db_path=self.db, now_ms=due + 180_000,
                        candle_fn=outage, dry=False)
        self.assertEqual(out["outcomes"][0]["status"], "labelled")
        self.assertTrue(out["outcomes"][0].get("already"))
        self.assertEqual(LW._attempts_of(self.con, "b" * 64)[0], 0)

    def test_censor_cannot_overwrite_a_label(self):
        due = self._seal()
        LW.settle(db_path=self.db, now_ms=due + 120_000,
                  candle_fn=lambda *, due_ms: candle_for(due_ms), dry=False)

        def outage(*, due_ms):
            raise ConnectionError("down")

        for _ in range(LW.MAX_FAILURES + 2):
            LW.settle(db_path=self.db, now_ms=due + 900_000,
                      candle_fn=outage, dry=False)
        state = self.con.execute(
            "SELECT label_state FROM tier2_outcomes").fetchone()[0]
        self.assertEqual(state, "labelled")

    def test_dry_does_not_create_the_attempts_table(self):
        db = self.dir / "fresh.sqlite"
        con = T2.open_tier2(db)
        T2.open_attempts(con)
        ev = {"event_id": "c" * 64, "cohort": "geopolitics",
              "published_ms": NOW - 120_000, "observed_ms": NOW - 60_000}
        due = T2.mock_triple(con=con, event=ev, now_ms=NOW,
                             attestation_path=self.dir / "a2.jsonl")["due_ms"]
        con.execute("DROP TABLE IF EXISTS tier2_label_attempts")
        con.commit()
        con.close()

        def outage(*, due_ms):
            raise ConnectionError("down")

        LW.settle(db_path=db, now_ms=due + 120_000, candle_fn=outage, dry=True)
        con = sqlite3.connect(str(db))
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        self.assertNotIn("tier2_label_attempts", tables)

    def test_four_processes_mixing_success_and_failure(self):
        import subprocess
        due = self._seal()
        ok_script = f"""
import sys
sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})
import tier2_label_watch as LW
due = {due}
def candle(*, due_ms):
    o = (due_ms // 60000) * 60000
    if o < due_ms: o += 60000
    while o + 59999 - due_ms >= 60000: o -= 60000
    return {{"symbol": "BTCUSDT", "closed": True, "open_ms": o,
             "close_ms": o + 59999, "close": 64000.0,
             "source": "binance_public_1m_kline"}}
print(LW.settle(db_path={str(self.db)!r}, now_ms=due + 120000,
                candle_fn=candle, dry=False)["outcomes"][0]["status"])
"""
        fail_script = ok_script.replace(
            "def candle(*, due_ms):",
            "def candle(*, due_ms):\n    raise ConnectionError('down')\ndef _x(*, due_ms):")
        procs = []
        for i in range(4):
            script = ok_script if i % 2 == 0 else fail_script
            procs.append(subprocess.Popen(["/usr/bin/python3", "-c", script],
                                          stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, text=True))
        outs = []
        for p in procs:
            o, e = p.communicate(timeout=25)
            outs.append(o.strip() or ("ERR:" + e.strip()[-120:]))
        rows = self.con.execute(
            "SELECT COUNT(*) FROM tier2_outcomes").fetchone()[0]
        self.assertEqual(rows, 1, outs)


class CensoredMetricTests(unittest.TestCase):
    """A censored row must never be read as a zero return."""

    def test_censored_is_excluded_from_labelled_queries(self):
        con = T2.open_tier2(Path(tempfile.mkdtemp()) / "m.sqlite")
        con.execute("INSERT INTO tier2_outcomes VALUES('e1',1,0.0,1,'unavailable',-1,'censored')")
        con.execute("INSERT INTO tier2_outcomes VALUES('e2',1,64000.0,1,'binance_public_1m_close',500,'labelled')")
        con.commit()
        labelled = [r[0] for r in con.execute(
            "SELECT event_id FROM tier2_outcomes WHERE label_state='labelled'")]
        self.assertEqual(labelled, ["e2"])
        self.assertNotIn("e1", labelled)
        self.assertEqual(con.execute(
            "SELECT COUNT(*) FROM tier2_outcomes WHERE label_state='labelled' "
            "AND price <= 0").fetchone()[0], 0)
        con.close()

if __name__ == "__main__":
    unittest.main()
