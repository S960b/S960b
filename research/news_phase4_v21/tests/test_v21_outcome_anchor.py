"""NEWS v2.1 — 4h label anchor tests.

The previous selector took the hourly candle ENDING BEFORE the due instant,
which reported a 3h-to-4h move as a 4h outcome. These tests pin the corrected
rule: the first 1m candle that CLOSES at or after due, never before it.
"""
import unittest

import v21_paper_prep as P

MIN = 60_000
HOUR = 3_600_000
BASE = 1_800_000_000_000  # exact hour boundary


def m1(open_ms, price):
    return (open_ms, open_ms + MIN - 1, price)


def hour_range(hour_ms, prices=None):
    """26 closed hourly candles, hour-aligned, the last closing just before it."""
    out = []
    last_open = (hour_ms // HOUR) * HOUR - HOUR
    for i in range(26):
        open_ms = last_open - (25 - i) * HOUR
        out.append((open_ms, open_ms + HOUR - 1,
                    100_000.0 + i if prices is None else prices[i]))
    return out


def minutes_from(start_ms, count, price=50_000.0):
    return [m1(start_ms + i * MIN, price + i) for i in range(count)]


class MidHourDueTests(unittest.TestCase):
    def test_due_in_middle_of_hour_takes_the_covering_minute(self):
        due = BASE + 10 * MIN + 30_000          # x:10:30
        now = due + 5 * MIN
        candles = minutes_from(BASE + 10 * MIN, 5)
        chosen = P.select_exit_candle(candles, due, now)
        self.assertEqual(chosen["exit_ms"], BASE + 11 * MIN - 1)
        self.assertEqual(chosen["drift_ms"], 29_999)
        self.assertLessEqual(chosen["drift_ms"], 60_000)
        self.assertGreaterEqual(chosen["exit_ms"], due,
                                "a label may never anchor before the horizon")

    def test_due_exactly_on_minute_boundary(self):
        due = BASE + 10 * MIN
        now = due + 3 * MIN
        candles = minutes_from(BASE + 10 * MIN, 3)
        chosen = P.select_exit_candle(candles, due, now)
        self.assertEqual(chosen["exit_ms"], due + MIN - 1)
        self.assertEqual(chosen["drift_ms"], MIN - 1)

    def test_due_one_ms_before_minute_boundary(self):
        # due = x:10:00-1ms is inside the minute that STARTED at x:09:00, so that
        # minute is the one covering it and its close is 1 minute later.
        due = BASE + 10 * MIN - 1
        now = due + 5 * MIN
        candles = minutes_from(BASE + 9 * MIN, 5)
        chosen = P.select_exit_candle(candles, due, now)
        self.assertEqual(chosen["exit_ms"], BASE + 10 * MIN - 1)
        self.assertEqual(chosen["drift_ms"], 0,
                         "close coincides with the horizon: zero drift")


class BoundaryTests(unittest.TestCase):
    def test_due_exactly_at_hour_boundary(self):
        due = BASE
        now = due + 2 * MIN
        candles = minutes_from(BASE, 3)
        chosen = P.select_exit_candle(candles, due, now)
        self.assertEqual(chosen["exit_ms"], due + MIN - 1)
        self.assertEqual(chosen["drift_ms"], MIN - 1)

    def test_candle_ending_before_due_is_never_used(self):
        # Only pre-due candles are available: the outcome must stay unavailable
        # instead of being anchored to the previous minute's close.
        due = BASE + 10 * MIN + 30_000
        now = due + 5 * MIN
        candles = [m1(BASE + 8 * MIN, 1.0), m1(BASE + 9 * MIN, 2.0)]
        with self.assertRaises(P.PrepError) as ctx:
            P.select_exit_candle(candles, due, now)
        self.assertEqual(str(ctx.exception), "label_candle_unavailable")

    def test_gap_after_due_is_refused_not_skipped(self):
        # A range that jumps past the due minute must not silently label it.
        due = BASE + 10 * MIN + 30_000
        now = due + 10 * MIN
        candles = [m1(BASE + 12 * MIN, 3.0)]
        with self.assertRaises(P.PrepError) as ctx:
            P.select_exit_candle(candles, due, now)
        self.assertEqual(str(ctx.exception), "label_candle_unavailable")

    def test_unclosed_candle_is_refused(self):
        due = BASE + 10 * MIN
        now = BASE + 10 * MIN + 20_000       # inside the covering minute
        candles = minutes_from(BASE + 10 * MIN, 2)
        with self.assertRaises(P.PrepError) as ctx:
            P.select_exit_candle(candles, due, now)
        self.assertEqual(str(ctx.exception), "label_candle_unavailable")

    def test_drift_over_one_minute_is_refused(self):
        due = BASE + 10 * MIN
        now = due + 10 * MIN
        candles = [m1(BASE + 12 * MIN, 5.0)]   # opens after due by 2 min
        with self.assertRaises(P.PrepError) as ctx:
            P.select_exit_candle(candles, due, now)
        self.assertEqual(str(ctx.exception), "label_candle_unavailable")


class DowntimeTests(unittest.TestCase):
    def test_late_callback_after_30h_still_anchors_at_due(self):
        due = BASE
        now = due + 30 * HOUR
        candles = minutes_from(BASE, 5)
        chosen = P.select_exit_candle(candles, due, now)
        self.assertEqual(chosen["exit_ms"], BASE + MIN - 1)
        self.assertEqual(chosen["drift_ms"], MIN - 1)
        self.assertLess(chosen["exit_ms"] - due, 2 * MIN)

    def test_bounded_range_by_due_not_latest_hours(self):
        seen = {}

        due_ms = BASE + 3 * HOUR + 17 * MIN

        def opener(url, timeout=0):
            seen["url"] = url
            start = (due_ms // MIN) * MIN
            candles = minutes_from(start, 5)

            class R:
                def read(self_inner):
                    import json
                    return json.dumps(
                        [[o, "", "", "", str(p), "", c] for o, c, p in candles]
                    ).encode()

                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *a):
                    return False
            return R()

        due = BASE + 3 * HOUR + 17 * MIN
        now = due + 40 * HOUR            # service was down for a day and a half
        chosen = P.resolve_exit(due, now, opener=opener)
        self.assertIn(f"startTime={(due // MIN) * MIN}", seen["url"])
        self.assertIn("interval=1m", seen["url"])
        self.assertEqual(chosen["exit_ms"], due + MIN - 1)
        self.assertEqual(chosen["drift_ms"], MIN - 1)

    def test_missing_data_leaves_pending_with_reason(self):
        def empty_opener(url, timeout=0):
            class R:
                def read(self_inner):
                    return b"[]"

                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *a):
                    return False
            return R()

        with self.assertRaises(P.PrepError) as ctx:
            P.resolve_exit(BASE, BASE + 10 * MIN, opener=empty_opener)
        self.assertEqual(str(ctx.exception), "label_candle_unavailable")

    def test_fetch_failure_is_reported_not_guessed(self):
        def boom(url, timeout=0):
            raise OSError("network down")

        with self.assertRaises(P.PrepError) as ctx:
            P.resolve_exit(BASE, BASE + 10 * MIN, opener=boom)
        self.assertTrue(str(ctx.exception).startswith("label_fetch_failed"))


class OldSelectorTests(unittest.TestCase):
    """Proves the old rule and the new rule disagree, and why."""

    @staticmethod
    def old_rule(hourly, due):
        for open_ms, close_ms, price in hourly:
            if close_ms <= due < close_ms + HOUR:
                return close_ms, price
        return None

    def test_old_code_picks_a_pre_due_close(self):
        # Due at x:10:30. The old rule matched the hour CLOSING at x:00:00-1ms,
        # i.e. 10.5 minutes early - and for later hours, up to 59.99 minutes.
        due = BASE + 10 * MIN + 30_000
        hourly = hour_range(BASE + HOUR)
        old = self.old_rule(hourly, due)
        self.assertIsNotNone(old)
        self.assertLess(old[0], due, "old rule anchors BEFORE the horizon")
        self.assertGreaterEqual(due - old[0], 10 * MIN)
        # Worst case: due one millisecond after an hour boundary would have been
        # labelled with the previous hour's close, an hour short of the horizon.
        worst_due = BASE + HOUR + 59 * MIN
        worst = self.old_rule(hourly, worst_due)
        self.assertIsNotNone(worst)
        self.assertGreater(worst_due - worst[0], 59 * MIN,
                           "up to an hour short of the horizon")

    def test_new_code_refuses_the_pre_due_hour(self):
        due = BASE + 10 * MIN + 30_000
        now = due + 5 * MIN
        with self.assertRaises(P.PrepError):
            P.select_exit_candle(hour_range(BASE + HOUR), due, now)

    def test_new_code_uses_the_1m_close_after_due(self):
        due = BASE + 10 * MIN + 30_000
        now = due + 5 * MIN
        chosen = P.select_exit_candle(minutes_from(BASE + 10 * MIN, 5),
                                      due, now)
        self.assertGreaterEqual(chosen["exit_ms"], due)
        self.assertLess(chosen["exit_ms"] - due, MIN)


if __name__ == "__main__":
    unittest.main()
