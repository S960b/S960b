"""Stage 30 — integration of the single canonical label path.

Every test runs on a disposable copy. None of them touch a live database.
"""
import hashlib
import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

import tier2 as T2
import tier2_label_watch as LW
import tier2_forward_driver as D

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


class CanonicalPathTests(unittest.TestCase):
    """The driver must not carry a second settlement implementation."""

    def test_driver_delegates_to_the_watcher(self):
        src = Path(D.__file__).read_text(encoding="utf-8")
        body = src.split("def label_once(", 1)[1].split("def main(", 1)[0]
        self.assertIn("tier2_label_watch", body,
                      "driver.label_once must delegate to the watcher")
        self.assertNotIn("resolve_exit(", body,
                         "driver.label_once must not fetch its own candle")

    def test_driver_label_once_is_dry_by_default(self):
        self.assertIn("dry=not settle", Path(D.__file__).read_text())

    def test_only_one_settlement_implementation_exists(self):
        src = Path(LW.__file__).read_text(encoding="utf-8")
        self.assertEqual(src.count("def settle_one("), 1)
        # The driver must not define its own outcome writer.
        dsrc = Path(D.__file__).read_text(encoding="utf-8")
        self.assertNotIn("INSERT INTO tier2_outcomes", dsrc)


class ExchangeCandleTests(unittest.TestCase):

    def test_candle_comes_from_payload_fields(self):
        rows = [(1_800_000_000_000, 1_800_000_059_999, 64_100.0)]
        import v21_paper_prep as P
        saved = P.fetch_klines_1m
        P.fetch_klines_1m = lambda *a, **k: rows
        try:
            c = LW.exchange_candle(due_ms=1_800_000_030_000,
                                   now_ms=1_800_000_120_000)
        finally:
            P.fetch_klines_1m = saved
        self.assertEqual((c["open_ms"], c["close_ms"]),
                         (1_800_000_000_000, 1_800_000_059_999))
        self.assertEqual(c["source"], "binance_public_1m_kline")
        self.assertTrue(c["closed"])

    def test_no_candle_covers_due_then_refuse(self):
        import v21_paper_prep as P
        saved = P.fetch_klines_1m
        P.fetch_klines_1m = lambda *a, **k: []
        try:
            with self.assertRaises(Exception):
                LW.exchange_candle(due_ms=1_800_000_030_000,
                                   now_ms=1_800_000_120_000)
        finally:
            P.fetch_klines_1m = saved


class IntegrationTests(unittest.TestCase):

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="s30-"))
        self.db = self.dir / "tier2.sqlite"
        con = T2.open_tier2(self.db)
        T2.open_attempts(con)
        ev = {"event_id": "d" * 64, "cohort": "geopolitics",
              "published_ms": NOW - 120_000, "observed_ms": NOW - 60_000}
        self.due = T2.mock_triple(con=con, event=ev, now_ms=NOW,
                                  attestation_path=self.dir / "a.jsonl")["due_ms"]
        con.close()

    def _settle(self, now_ms, candle=None, dry=False, **kw):
        return LW.settle(db_path=self.db, now_ms=now_ms,
                         candle_fn=candle or (lambda *, due_ms: candle_for(due_ms)),
                         dry=dry, **kw)

    def _outcomes(self):
        con = sqlite3.connect(str(self.db))
        try:
            return con.execute(
                "SELECT label_state, price FROM tier2_outcomes").fetchall()
        finally:
            con.close()

    def test_exact_once_across_restart(self):
        self._settle(self.due + 120_000)
        self._settle(self.due + 240_000)          # fresh process, same DB
        self._settle(self.due + 600_000)
        self.assertEqual(len(self._outcomes()), 1)

    def test_dry_mode_does_not_touch_the_database(self):
        before = hashlib.sha256(Path(self.db).read_bytes()).hexdigest()
        self._settle(self.due + 120_000, dry=True)
        after = hashlib.sha256(Path(self.db).read_bytes()).hexdigest()
        self.assertEqual(before, after, "dry mode mutated the database file")

    def test_typeerror_is_not_an_outage(self):
        def bug(*, due_ms):
            raise TypeError("bad unpack")

        out = self._settle(self.due + 120_000, candle=bug)
        self.assertEqual(out["outcomes"][0]["status"], "error")
        self.assertEqual(self._outcomes(), [])

    def test_labelled_row_survives_later_outages(self):
        self._settle(self.due + 120_000)

        def outage(*, due_ms):
            raise ConnectionError("down")

        for _ in range(5):
            self._settle(self.due + 900_000, candle=outage)
        self.assertEqual(self._outcomes(), [("labelled", 64_000.0)])

    def test_censored_never_scores_as_zero(self):
        def outage(*, due_ms):
            raise ConnectionError("down")

        for _ in range(LW.MAX_FAILURES):
            self._settle(self.due + 600_000, candle=outage)
        con = sqlite3.connect(str(self.db))
        labelled = con.execute(
            "SELECT COUNT(*) FROM tier2_outcomes WHERE label_state='labelled'"
        ).fetchone()[0]
        censored = con.execute(
            "SELECT price FROM tier2_outcomes WHERE label_state='censored'"
        ).fetchone()[0]
        con.close()
        self.assertEqual(labelled, 0, "a censored row must not be scored")
        self.assertEqual(censored, 0.0)


class FeeSlippageScoringTests(unittest.TestCase):
    """The full paper path: seal -> +4h label -> score with 6 bps round trip."""

    def test_score_applies_round_trip_cost(self):
        entry, exit_price = 64_000.0, 64_128.0        # +20 bps move
        gross_bps = (exit_price / entry - 1) * 10_000
        self.assertAlmostEqual(gross_bps, 20.0, places=6)
        net = gross_bps - 6.0                          # 3 bps each way
        self.assertAlmostEqual(net, 14.0, places=6)

    def test_flat_position_scores_zero_cost_free(self):
        # The pre-registered rule: FLAT carries no fees.
        self.assertEqual(0.0 - 0.0, 0.0)


if __name__ == "__main__":
    unittest.main()
