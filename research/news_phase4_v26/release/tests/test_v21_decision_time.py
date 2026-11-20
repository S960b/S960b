"""NEWS v2.1 — decision time vs market cutoff (no-future-leak regressions).

Collapsing the two clocks made a 10:50 run look like a 10:00 run: a story
published at 10:35 would have been rejected outright, and a story published at
08:55 would have looked two hours fresher than it was.
"""
import os
import tempfile
import unittest

import v21_dispatch as D
import v21_paper_prep as P

HOUR = 3_600_000
MIN = 60_000
BASE = 1_800_000_000_000  # an exact hour boundary
H10_00 = BASE
CLOSES = [100_000 + i for i in range(26)]


def candles_before(hour_ms):
    last_open = hour_ms - HOUR
    return [(last_open - (25 - i) * HOUR,
             last_open - (25 - i) * HOUR + HOUR - 1, p)
            for i, p in enumerate(CLOSES)]


def event(published_ms, observed_ms=None, cohort="context_news"):
    return {"event_id": "e" * 64, "source": "coindesk", "cohort": cohort,
            "published_ms": published_ms,
            "observed_ms": observed_ms if observed_ms is not None
            else published_ms + 30_000,
            "title": "Bitcoin miners halt operations", "summary": "supply",
            "source_kind": "media"}


class DecisionTimeTests(unittest.TestCase):
    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.con = D.open_db(self.db, create=True)

    def tearDown(self):
        self.con.close()
        for suf in ("", "-wal", "-shm"):
            try:
                os.unlink(self.db + suf)
            except FileNotFoundError:
                pass

    # --- the two timestamps are never the same value ---------------------
    def test_decision_time_is_not_the_market_cutoff(self):
        decision = H10_00 + 37 * MIN
        market = P.market_features(candles_before(H10_00), H10_00)
        r = P.prepare(None, self.con, event(H10_00 + 35 * MIN), enabled=True,
                      now_ms=decision, market_cutoff_ms=H10_00,
                      market=market)
        self.assertEqual(r["decision_time_ms"], decision)
        self.assertEqual(r["market_cutoff_ms"], H10_00)
        self.assertNotEqual(r["decision_time_ms"], r["cutoff_ms"])
        self.assertLess(r["cutoff_ms"], r["decision_time_ms"])

    def test_1037_run_with_1035_publication_is_allowed(self):
        # Published 2 min ago, decided at 10:37. The old code decided at 10:00
        # and would have thrown event_outside_cohort_window.
        decision = H10_00 + 37 * MIN
        market = P.market_features(candles_before(H10_00), H10_00)
        r = P.prepare(None, self.con,
                      event(H10_00 + 35 * MIN, cohort="fast_verified"),
                      enabled=True, now_ms=decision, market_cutoff_ms=H10_00,
                      market=market)
        self.assertEqual(r["status"], "ready")
        self.assertEqual(r["age_ms"], 2 * MIN)

    def test_1059_run_with_0855_publication_sees_true_age(self):
        # 2 h 4 min old at decision time: outside the 2 h window.
        decision = H10_00 + 59 * MIN - 1
        market = P.market_features(candles_before(H10_00), H10_00)
        with self.assertRaises(P.PrepError) as ctx:
            P.prepare(None, self.con, event(H10_00 - 65 * MIN), enabled=True,
                      now_ms=decision, market_cutoff_ms=H10_00, market=market)
        self.assertEqual(str(ctx.exception), "event_outside_cohort_window")

    def test_1059_run_with_0855_is_not_reported_as_fresh(self):
        # Just inside the window: age must be ~1 h 59 min, not ~1 h.
        decision = H10_00 + 59 * MIN
        published = H10_00 - 60 * MIN
        market = P.market_features(candles_before(H10_00), H10_00)
        r = P.prepare(None, self.con, event(published), enabled=True,
                      now_ms=decision, market_cutoff_ms=H10_00, market=market)
        self.assertEqual(r["age_ms"], decision - published)
        self.assertGreater(r["age_ms"], HOUR)
        self.assertNotEqual(r["age_ms"], decision - H10_00)

    def test_market_after_decision_rejected(self):
        decision = H10_00 + 10 * MIN
        with self.assertRaises(P.PrepError) as ctx:
            P.prepare(None, self.con, event(H10_00), enabled=True,
                      now_ms=decision,
                      market_cutoff_ms=H10_00 + HOUR,
                      market=P.market_features(candles_before(H10_00 + HOUR),
                                               H10_00 + HOUR))
        self.assertEqual(str(ctx.exception), "market_after_decision")

    def test_candle_closing_after_decision_rejected(self):
        decision = H10_00 + 10 * MIN
        with self.assertRaises(P.PrepError) as ctx:
            P.prepare(None, self.con, event(H10_00), enabled=True,
                      now_ms=decision,
                      market_cutoff_ms=H10_00 + HOUR,
                      market={"candle_count": 26,
                              "candle_closes_ms": H10_00 + HOUR + 5})
        self.assertEqual(str(ctx.exception), "market_after_decision")

    def test_observed_in_future_rejected(self):
        decision = H10_00 + 10 * MIN
        market = P.market_features(candles_before(H10_00), H10_00)
        with self.assertRaises(P.PrepError) as ctx:
            P.prepare(None, self.con,
                      event(H10_00, observed_ms=decision + MIN), enabled=True,
                      now_ms=decision, market_cutoff_ms=H10_00, market=market)
        self.assertEqual(str(ctx.exception), "observed_in_future")

    def test_failed_preflight_reserves_nothing(self):
        decision = H10_00 + 37 * MIN
        with self.assertRaises(P.PrepError):
            P.prepare(None, self.con, event(H10_00 + 35 * MIN), enabled=True,
                      now_ms=decision, market_cutoff_ms=H10_00, market=None)
        st = D.status(self.con, decision)
        self.assertEqual(st["synthetic"]["calls_today"], 0)

    def test_preflight_flag_is_required_when_supplied(self):
        decision = H10_00 + 37 * MIN
        market = P.market_features(candles_before(H10_00), H10_00)
        with self.assertRaises(P.PrepError) as ctx:
            P.prepare(None, self.con, event(H10_00 + 35 * MIN), enabled=True,
                      now_ms=decision, market_cutoff_ms=H10_00, market=market,
                      preflight=False)
        self.assertEqual(str(ctx.exception), "preflight_not_passed")


if __name__ == "__main__":
    unittest.main()
