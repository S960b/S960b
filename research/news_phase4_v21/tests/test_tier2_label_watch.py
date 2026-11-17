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
            "close_ms": open_ms + 59_999, "close": price}


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
        self.assertEqual(rows, [("labelled", 64_000.0, "binance_public_1m_close")])

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
             "close_ms": o + 59999, "close": 64000.0}}
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


if __name__ == "__main__":
    unittest.main()
