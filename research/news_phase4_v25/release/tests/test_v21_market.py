"""NEWS v2.1 — market feature guard tests (offline).

Covers the closeTime convention measured against the live Binance endpoint
(closeTime = openTime + 3600000 - 1) and the hard no-future-leak rules.
"""
import unittest

import v21_paper_prep as P

HOUR = 3_600_000
NOW = 1_800_000_000_000
LAST_OPEN = ((NOW - 5 * 60_000) // HOUR) * HOUR - HOUR
CLOSES = [100_000 + i for i in range(26)]
CANDLES = [(LAST_OPEN - (25 - i) * HOUR,
            LAST_OPEN - (25 - i) * HOUR + HOUR - 1, p)
           for i, p in enumerate(CLOSES)]


class CloseTimeConventionTests(unittest.TestCase):
    def test_binance_close_time_minus_one_ms_is_accepted(self):
        # Live API returns closeTime = open + interval - 1 ms.
        candles = [(open_ms, open_ms + HOUR - 1, p) for open_ms, _c, p in CANDLES]
        mk = P.market_features(candles, NOW)
        self.assertEqual(mk["candle_count"], 26)

    def test_legacy_minus_1000ms_is_accepted(self):
        candles = [(o, o + HOUR - 1000, p) for o, _c, p in CANDLES]
        self.assertEqual(P.market_features(candles, NOW)["candle_count"], 26)

    def test_wrong_close_offset_is_rejected(self):
        candles = list(CANDLES)
        o, _c, p = candles[3]
        candles[3] = (o, o + HOUR - 777, p)
        with self.assertRaises(P.PrepError) as ctx:
            P.market_features(candles, NOW)
        self.assertEqual(str(ctx.exception), "invalid_candle_bounds")


class FutureLeakTests(unittest.TestCase):
    def test_forming_candle_is_dropped_not_used(self):
        # A still-forming candle after the cutoff must be discarded entirely.
        extra = (LAST_OPEN + HOUR, LAST_OPEN + 2 * HOUR - 1, 999_999.0)
        cutoff = LAST_OPEN + HOUR
        mk = P.market_features([extra] + list(CANDLES), cutoff)
        self.assertEqual(mk["candle_count"], 26)
        self.assertAlmostEqual(
            mk["return_1h"], CLOSES[-1] / CLOSES[-2] - 1)

    def test_cutoff_must_be_hour_aligned(self):
        with self.assertRaises(P.PrepError) as ctx:
            P.market_features(CANDLES, NOW + 1234, require_cutoff_on_hour=True)
        self.assertEqual(str(ctx.exception), "cutoff_not_hour_aligned")

    def test_decision_cutoff_rounds_down_to_closed_hour(self):
        skewed = NOW + 1234
        self.assertEqual(P.decision_cutoff(skewed) % HOUR, 0)
        self.assertLess(P.decision_cutoff(skewed), skewed)
        self.assertEqual(P.decision_cutoff(skewed),
                         P.decision_cutoff(skewed + 5))

    def test_stale_market_data_rejected(self):
        shift = 10 * HOUR
        old = list(CANDLES)
        for i, (o, c, p) in enumerate(old):
            old[i] = ((o - o % HOUR) - shift + (o % HOUR), c - shift, p)
        with self.assertRaises(P.PrepError) as ctx:
            P.market_features(old, P.decision_cutoff(NOW))
        self.assertEqual(str(ctx.exception), "stale_market_data")


class LiveFetchShapeTests(unittest.TestCase):
    def test_fetch_klines_rejects_fixture_mode(self):
        with self.assertRaises(P.PrepError) as ctx:
            P.fetch_market("fixture", P.decision_cutoff(NOW))
        self.assertEqual(str(ctx.exception),
                         "fixture_market_must_be_supplied")

    def test_fetch_market_requires_aligned_cutoff(self):
        with self.assertRaises(P.PrepError):
            P.fetch_market("production", NOW + 1234)


if __name__ == "__main__":
    unittest.main()
